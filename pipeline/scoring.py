import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pipeline.db import (
    get_connection,
    get_linked_signals_for_opportunity_space,
    get_all_opportunity_spaces,
    get_unscored_opportunity_spaces,
    get_opportunity_spaces_missing_right_to_win,
    get_opportunity_spaces_with_fallback_scores,
    get_opportunity_spaces_with_old_scores,
    get_latest_scores,
    insert_score,
    insert_right_to_win_score,
    update_opportunity_space_enrichment,
)
from pipeline.config import (
    ORANGE_BUSINESS_ASSETS,
    ANALYST_RECOGNITION,
    CAPABILITY_STATS,
    CUSTOMER_REFERENCES,
    ROLES,
    BUYER_PERSONAS,
    GEOS_PROMPT,
    HORIZONS,
    DOMAINS_TAXONOMY,
    TRUST_CRITICAL_VERTICALS,
    map_to_value_proposition,
    OPPORTUNITY_COUNT_BY_VERTICAL,
    PIPELINE_VALUE_BY_VERTICAL,
    TED_LOOKBACK_DAYS,
)
from llm.llm_client import get_llm_json

WEIGHTS = {
    "market_signal_strength": 0.3,
    "source_diversity": 0.2,
    "evidence_quality": 0.25,
    "novelty_momentum": 0.1,
    "strategic_relevance": 0.15,
}
MARKET_SIGNAL_CAP = 56
SOURCE_DIVERSITY_CAP = 40
EVIDENCE_QUALITY_MAX_SIGNALS = 15
ENRICHMENT_SAMPLE_SIZE = 10


def market_signal_strength(signals) -> float:
    return min(10.0, len(signals) / MARKET_SIGNAL_CAP * 10)


def source_diversity(signals) -> float:
    distinct_sources = {s["source_name"] for s in signals}
    return min(10.0, len(distinct_sources) / SOURCE_DIVERSITY_CAP * 10)


def _parse_signal_date(signal):
    for key in ("published_date", "collected_at"):
        try:
            raw = signal[key]
        except (KeyError, IndexError):
            continue
        if not raw:
            continue
        raw = raw.strip()
        dt = None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
        if dt is None:
            try:
                dt = parsedate_to_datetime(raw)
            except (ValueError, TypeError):
                pass
        if dt is None:
            try:
                dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    return None


def novelty_momentum(signals) -> float:
    if not signals:
        return 0.0
    parsed_dates = [
        d for d in (_parse_signal_date(s) for s in signals) if d is not None
    ]
    if len(parsed_dates) < 3:
        return 5.0
    parsed_dates.sort()
    oldest = parsed_dates[0]
    now = datetime.now(timezone.utc)
    span_seconds = (now - oldest).total_seconds()
    if span_seconds <= 0:
        return 5.0
    recent_third_start = now - (now - oldest) / 3
    recent_count = sum((1 for d in parsed_dates if d >= recent_third_start))
    recent_share = recent_count / len(parsed_dates)
    return round(recent_share * 10, 2)


URGENT_SIGNAL_TYPES = {"regulation", "buying_signal"}
URGENCY_PERCENTILE = 95
URGENCY_MIN_SCALING_POINT = 1.0
URGENCY_CAP = 6.0
NOVELTY_URGENCY_WEIGHT = 2.0


def _urgency_weighted(signals) -> float:
    weighted = 0.0
    for s in signals:
        if s["signal_type"] == "regulation":
            weighted += 1.0
        elif s["signal_type"] == "buying_signal":
            dt = _parse_signal_date(s)
            if dt is None:
                weighted += 0.5
            else:
                age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
                weighted += max(0.0, 1 - age_days / TED_LOOKBACK_DAYS)
    if len(signals) >= 3:
        weighted += NOVELTY_URGENCY_WEIGHT * (novelty_momentum(signals) / 10.0)
    return weighted


def compute_urgency_scaling_point(conn, opportunity_space_ids=None) -> float:
    import statistics

    if opportunity_space_ids is None:
        rows = get_latest_scores(conn)
        ids = [r["id"] for r in rows]
    else:
        ids = list(opportunity_space_ids)
    weighted_values = [
        _urgency_weighted(get_linked_signals_for_opportunity_space(conn, os_id))
        for os_id in ids
    ]
    weighted_values = [w for w in weighted_values if w > 0]
    if len(weighted_values) < 2:
        return URGENCY_CAP
    percentile_95 = statistics.quantiles(weighted_values, n=100, method="inclusive")[
        URGENCY_PERCENTILE - 1
    ]
    percentile_95 = min(percentile_95, max(weighted_values))
    return max(percentile_95, URGENCY_MIN_SCALING_POINT)


def urgency_score(signals, scaling_point=URGENCY_CAP) -> float:
    if not signals:
        return 0.0
    weighted = _urgency_weighted(signals)
    return round(min(10.0, weighted / scaling_point * 10), 2)


EVIDENCE_QUALITY_SYSTEM_PROMPT = 'You are scoring the credibility and relevance of\nmarket signals for a B2B innovation radar. You must respond with ONLY a JSON object,\nno preamble, no markdown fences: {"score": <0-10 number>, "justification": "<one sentence>"}.\nScore 10 = signals are specific, from diverse credible sources (analyst reports,\nregulators, established media), and clearly relevant. Score 0 = signals are vague,\nsingle-source, vendor-marketing, or unrelated to the topic.'
STRATEGIC_RELEVANCE_SYSTEM_PROMPT = 'You are scoring how well an innovation opportunity\nfits Orange Business\'s ACTUAL, currently sellable product portfolio -- not just a generic\ndomain like "Cloud" or "Security", but a specific existing API/product.\n\nHere is the real Orange Business API catalog to check against:\n{asset_catalog}\n{analyst_recognition}\n{capability_stats}\n\nRespond with ONLY a JSON object, no preamble, no markdown fences:\n{{"score": <0-10 number>, "justification": "<one sentence citing the specific matching Orange Business asset by name, or explaining why none match>"}}.\n\nScore 10 = directly extends a specific asset above with clear enterprise value (name it).\nScore 5 = broadly fits an Orange Business domain but no specific asset matches well.\nScore 0 = unrelated to anything Orange Business currently sells.'
RIGHT_TO_WIN_SYSTEM_PROMPT = 'You are classifying an Orange Business opportunity space\non "portfolio distance": how close it is to something Orange Business can ALREADY sell,\nusing this real API/product catalog:\n{asset_catalog}\n{analyst_recognition}\n{capability_stats}\n{customer_references}\n\nClassify using exactly one of these levels:\nL0 = Direct offer: an existing asset above addresses this as-is.\nL1 = Bundle: two or more existing assets exist but are not packaged together yet.\nL2 = Partner-dependent: needs a capability an external partner has, not Orange itself.\nL3 = Adjacent: needs one new capability to be built or acquired -- close, but missing.\nL4 = White space: no plausible path from the current portfolio.\n\nRespond with ONLY a JSON object, no preamble, no markdown fences:\n{{"portfolio_distance": "L0"|"L1"|"L2"|"L3"|"L4", "right_to_win_score": <0-10, ONE DECIMAL PLACE (e.g. 6.7, not 6 or 7), where L0~9-10, L4~0-2>,\n  "matched_assets": ["<asset name(s) from the catalog that apply, empty list if L3/L4>"],\n  "justification": "<one sentence explaining the classification>"}}'
ENRICHMENT_SYSTEM_PROMPT = 'You are enriching a B2B innovation opportunity space with\nthe metadata a sales/presales team needs to act on it.\n\nValid roles (pick the ONE Orange Business team that should own this opportunity): {roles}\nValid buyer personas (pick the ONE most relevant contact on the customer side): {buyer_personas}\nValid geographies (pick 1-3 most relevant): {geos}\nValid horizons (pick exactly one): {horizons}\n  Now = sellable this quarter with what Orange Business already has\n  Next = sellable in 6-12 months, needs some development or partnership\n  Later = exploratory, research-stage, no clear delivery path yet\nValid business domains (pick the ONE that best fits, exact match required): {domains}\n\nAlso write ONE concrete next action PER ROLE below -- Strategist, Sales, and Presales each need\nto do something DIFFERENT with the same opportunity (e.g. Strategist commissions a deep-dive,\nSales opens a conversation, Presales prepares a bid brief). Do not repeat the same sentence\nfor all three.\n\nRespond with ONLY a JSON object, no preamble, no markdown fences:\n{{"role": "<one from the roles list>", "buyer_persona": "<one from the buyer personas list>",\n  "geography": ["<1-3 from the list>"], "horizon": "Now"|"Next"|"Later",\n  "domain": "<exact domain name from the list>",\n  "next_action_strategist": "<one concrete sentence for the Strategist/Innovator role>",\n  "next_action_sales": "<one concrete sentence for the Sales role>",\n  "next_action_presales": "<one concrete sentence for the Presales/Proposal role>"}}'


def _format_asset_catalog():
    return "\n".join(
        (f"- {a['name']} ({a['category']})" for a in ORANGE_BUSINESS_ASSETS)
    )


def _format_analyst_recognition():
    if not ANALYST_RECOGNITION:
        return ""
    lines = "\n".join(
        (f"- {a['fact']} (source: {a['source']})" for a in ANALYST_RECOGNITION)
    )
    return f"\nExternal validation (use to strengthen justifications where relevant):\n{lines}"


def _format_capability_stats():
    if not CAPABILITY_STATS:
        return ""
    lines = "\n".join(
        (f"- {c['stat']} (source: {c['source']})" for c in CAPABILITY_STATS)
    )
    return f"\nOrange Business scale/capability facts (cite only if directly relevant, e.g. delivery capacity or security posture):\n{lines}"


def _format_customer_references(vertical):
    matches = [c for c in CUSTOMER_REFERENCES if c.get("vertical") == vertical]
    if not matches:
        return ""
    lines = "\n".join((f"- {c['customer']} (source: {c['source']})" for c in matches))
    return f"\nVerified named customers of Orange Business in THIS vertical (cite if relevant -- an actual delivered customer is stronger evidence than a matching asset alone):\n{lines}"


def llm_evidence_quality(signals) -> tuple:
    if not signals:
        return (0.0, "No signals collected yet for this vertical.")
    titles = "\n".join(
        (
            f"- [{s['source_name']}] {s['title']}"
            for s in signals[:EVIDENCE_QUALITY_MAX_SIGNALS]
        )
    )
    prompt = f"Signals to evaluate:\n{titles}"
    result = get_llm_json(prompt, system_prompt=EVIDENCE_QUALITY_SYSTEM_PROMPT)
    if not result or "score" not in result:
        return (5.0, "LLM scoring unavailable -- neutral default used.")
    return (float(result["score"]), result.get("justification", ""))


def llm_strategic_relevance(vertical, use_case, technology, signals) -> tuple:
    prompt = f"Opportunity space: {vertical} x {use_case} x {technology}\nNumber of supporting signals: {len(signals)}"
    system_prompt = STRATEGIC_RELEVANCE_SYSTEM_PROMPT.format(
        asset_catalog=_format_asset_catalog(),
        analyst_recognition=_format_analyst_recognition(),
        capability_stats=_format_capability_stats(),
    )
    result = get_llm_json(prompt, system_prompt=system_prompt)
    if not result or "score" not in result:
        return (5.0, "LLM scoring unavailable -- neutral default used.")
    score = float(result["score"])
    justification = result.get("justification", "")
    value_prop = map_to_value_proposition(f"{vertical} {use_case} {technology}")
    if value_prop:
        justification += f" Maps to Orange's '{value_prop.value}' value proposition."
    if vertical in TRUST_CRITICAL_VERTICALS:
        score = min(10.0, score + 0.5)
        justification += f" +0.5 trust-critical bonus: Orange runs a dedicated {vertical} division (corporate deck, slide 12-14)."
    return (score, justification)


def crm_customer_overlap_bonus(vertical) -> float:
    count = sum((1 for c in CUSTOMER_REFERENCES if c.get("vertical") == vertical))
    return min(1.0, count * 0.5)


def pipeline_calibration_bonus(vertical) -> float:
    bonus = 0.0
    if OPPORTUNITY_COUNT_BY_VERTICAL.get(vertical):
        bonus += 0.3
    values = [v for v in PIPELINE_VALUE_BY_VERTICAL.values() if v]
    median_value = sorted(values)[len(values) // 2] if values else None
    if (
        median_value is not None
        and PIPELINE_VALUE_BY_VERTICAL.get(vertical, 0) >= median_value
    ):
        bonus += 0.3
    return bonus


def llm_right_to_win(vertical, use_case, technology):
    prompt = f"Opportunity space: {vertical} x {use_case} x {technology}"
    system_prompt = RIGHT_TO_WIN_SYSTEM_PROMPT.format(
        asset_catalog=_format_asset_catalog(),
        analyst_recognition=_format_analyst_recognition(),
        capability_stats=_format_capability_stats(),
        customer_references=_format_customer_references(vertical),
    )
    result = get_llm_json(prompt, system_prompt=system_prompt)
    if not result or "portfolio_distance" not in result:
        return (
            "L4",
            0.0,
            "",
            "LLM scoring unavailable -- defaulted to L4/0 (do not trust, re-run scoring).",
        )
    distance = result.get("portfolio_distance", "L4")
    score = float(result.get("right_to_win_score", 0))
    assets = ", ".join(result.get("matched_assets", []))
    justification = result.get("justification", "")
    crm_bonus = crm_customer_overlap_bonus(vertical)
    pipeline_bonus = pipeline_calibration_bonus(vertical)
    total_bonus = crm_bonus + pipeline_bonus
    if total_bonus:
        score = min(10.0, score + total_bonus)
        justification += f" +{total_bonus:.1f} calibration bonus (CRM customer overlap{(', opportunity count/pipeline value' if pipeline_bonus else '')})."
    return (distance, score, assets, justification)


def llm_enrich(vertical, use_case, technology, signals):
    sample_titles = "\n".join(
        (f"- {s['title']}" for s in signals[:ENRICHMENT_SAMPLE_SIZE])
    )
    prompt = f"Opportunity space: {vertical} x {use_case} x {technology}\nSample signals:\n{sample_titles}"
    domain_names = [d["name"] for d in DOMAINS_TAXONOMY]
    system_prompt = ENRICHMENT_SYSTEM_PROMPT.format(
        roles=", ".join(ROLES),
        buyer_personas=", ".join(BUYER_PERSONAS),
        geos=GEOS_PROMPT,
        horizons=", ".join(HORIZONS),
        domains=", ".join(domain_names),
    )
    result = get_llm_json(prompt, system_prompt=system_prompt)
    fallback_action = (
        "LLM enrichment unavailable -- review manually before showing to Sales."
    )
    if not result or "role" not in result:
        return {
            "role": None,
            "buyer_persona": None,
            "geography": None,
            "horizon": "Later",
            "domain": None,
            "next_action_strategist": fallback_action,
            "next_action_sales": fallback_action,
            "next_action_presales": fallback_action,
        }
    geography = result.get("geography", [])
    domain = result.get("domain")
    if domain not in domain_names:
        domain = None
    return {
        "role": result.get("role"),
        "buyer_persona": result.get("buyer_persona"),
        "geography": ", ".join(geography) if isinstance(geography, list) else geography,
        "horizon": result.get("horizon", "Later"),
        "domain": domain,
        "next_action_strategist": result.get("next_action_strategist", fallback_action),
        "next_action_sales": result.get("next_action_sales", fallback_action),
        "next_action_presales": result.get("next_action_presales", fallback_action),
        "next_action": result.get("next_action", ""),
    }


def score_opportunity_space(
    conn, opportunity_space_row, urgency_scaling_point=URGENCY_CAP
):
    signals = get_linked_signals_for_opportunity_space(
        conn, opportunity_space_row["id"]
    )
    evidence_score, evidence_justification = llm_evidence_quality(signals)
    relevance_score, relevance_justification = llm_strategic_relevance(
        opportunity_space_row["vertical"],
        opportunity_space_row["use_case"],
        opportunity_space_row["technology"],
        signals,
    )
    sub_scores = {
        "market_signal_strength": market_signal_strength(signals),
        "source_diversity": source_diversity(signals),
        "evidence_quality": evidence_score,
        "novelty_momentum": novelty_momentum(signals),
        "strategic_relevance": relevance_score,
    }
    urgency = urgency_score(signals, scaling_point=urgency_scaling_point)
    total = sum((sub_scores[k] * WEIGHTS[k] for k in WEIGHTS))
    insert_score(
        conn,
        opportunity_space_row["id"],
        sub_scores,
        round(total, 2),
        evidence_quality_justification=evidence_justification,
        strategic_relevance_justification=relevance_justification,
        urgency_score=urgency,
    )
    return (sub_scores, round(total, 2), urgency)


def score_all_opportunity_spaces(force=False, from_label=None, to_label=None):
    conn = get_connection()
    spaces = (
        get_all_opportunity_spaces(conn)
        if force
        else get_unscored_opportunity_spaces(conn)
    )
    repair_spaces = [] if force else get_opportunity_spaces_missing_right_to_win(conn)
    if repair_spaces:
        print(
            f"Repairing {len(repair_spaces)} opportunity space(s) left incomplete by an earlier interrupted run (has attractiveness score, missing right-to-win):"
        )
        for s in repair_spaces:
            print(
                f"  {s['label']} ({s['vertical']} x {s['use_case']} x {s['technology']})"
            )
        print()
    if force and from_label:
        before = len(spaces)
        spaces = [s for s in spaces if s["label"] >= from_label]
        print(
            f"--from {from_label}: skipping {before - len(spaces)} opportunity space(s) already done before the interruption.\n"
        )
    if force and to_label:
        before = len(spaces)
        spaces = [s for s in spaces if s["label"] <= to_label]
        print(
            f"--to {to_label}: keeping only {len(spaces)} of {before} opportunity space(s) up to and including this label.\n"
        )
    if not spaces and (not repair_spaces):
        print(
            "Nothing to score -- every opportunity space already has a score. Use --force to rescore everything anyway."
        )
        conn.close()
        return
    print(
        f"Scoring {len(spaces)} opportunity space(s){(' (forced rescore of everything)' if force else ' (unscored only)')}\n"
    )
    urgency_scaling_point = compute_urgency_scaling_point(
        conn,
        opportunity_space_ids=[s["id"] for s in spaces]
        + [s["id"] for s in repair_spaces],
    )
    print(
        f"Urgency scaling point this run (95th percentile of weighted urgent signals): {urgency_scaling_point:.2f} -- an OS at or above this weighted value scores 10/10 on urgency.\n"
    )
    for space in spaces:
        sub_scores, total, urgency = score_opportunity_space(
            conn, space, urgency_scaling_point=urgency_scaling_point
        )
        distance, rtw_score, assets, rtw_justification = llm_right_to_win(
            space["vertical"], space["use_case"], space["technology"]
        )
        insert_right_to_win_score(
            conn, space["id"], distance, rtw_score, assets, rtw_justification
        )
        print(
            f"{space['label']} ({space['vertical']} x {space['use_case']} x {space['technology']})"
        )
        print(f"  Attractiveness: {total}/10  {sub_scores}")
        print(f"  Urgency:        {urgency}/10")
        print(
            f"  Right-to-win:   {rtw_score}/10  [{distance}] assets: {assets or 'none'}"
        )
        print(f"  -> {rtw_justification}")
        if space["domain"] and (not force):
            print("  Enrichment: skipped (already enriched -- use --force to redo)")
        else:
            vertical = space["vertical"]
            signals = get_linked_signals_for_opportunity_space(conn, space["id"])
            enrichment = llm_enrich(
                vertical, space["use_case"], space["technology"], signals
            )
            update_opportunity_space_enrichment(
                conn,
                space["id"],
                role=enrichment["role"],
                buyer_persona=enrichment["buyer_persona"],
                geography=enrichment["geography"],
                horizon=enrichment["horizon"],
                domain=enrichment["domain"],
                next_action_strategist=enrichment["next_action_strategist"],
                next_action_sales=enrichment["next_action_sales"],
                next_action_presales=enrichment["next_action_presales"],
            )
            print(
                f"  Enrichment: role={enrichment['role']}  buyer_persona={enrichment['buyer_persona']}  geography={enrichment['geography']}  horizon={enrichment['horizon']}  domain={enrichment['domain']}"
            )
            print(f"  Next action (Strategist): {enrichment['next_action_strategist']}")
            print(f"  Next action (Sales):      {enrichment['next_action_sales']}")
            print(f"  Next action (Presales):   {enrichment['next_action_presales']}")
        print()
    for space in repair_spaces:
        distance, rtw_score, assets, rtw_justification = llm_right_to_win(
            space["vertical"], space["use_case"], space["technology"]
        )
        insert_right_to_win_score(
            conn, space["id"], distance, rtw_score, assets, rtw_justification
        )
        print(
            f"REPAIRED {space['label']}: Right-to-win {rtw_score}/10 [{distance}] assets: {assets or 'none'}"
        )
        if not space["domain"]:
            vertical = space["vertical"]
            signals = get_linked_signals_for_opportunity_space(conn, space["id"])
            enrichment = llm_enrich(
                vertical, space["use_case"], space["technology"], signals
            )
            update_opportunity_space_enrichment(
                conn,
                space["id"],
                role=enrichment["role"],
                buyer_persona=enrichment["buyer_persona"],
                geography=enrichment["geography"],
                horizon=enrichment["horizon"],
                domain=enrichment["domain"],
                next_action_strategist=enrichment["next_action_strategist"],
                next_action_sales=enrichment["next_action_sales"],
                next_action_presales=enrichment["next_action_presales"],
            )
        print()
    recalibrate_urgency(conn)
    stale_ids = {s["id"] for s in get_opportunity_spaces_with_old_scores(conn)}
    if stale_ids:
        stale_rows = [r for r in get_latest_scores(conn) if r["id"] in stale_ids]
        if stale_rows:
            print(
                f"\nAuto-refreshing {len(stale_rows)} opportunity space(s) scored more than 3 days ago (free, deterministic only -- run `radar_cli.py link` first if new signals should count):\n"
            )
            recalibrate_deterministic_scores(conn, rows=stale_rows)
    conn.close()


def recalibrate_deterministic_scores(conn=None, rows=None):
    close_after = conn is None
    if conn is None:
        conn = get_connection()
    if rows is None:
        rows = get_latest_scores(conn)
    if not rows:
        print(
            "No scored opportunity spaces found -- nothing to refresh. Run `python -m pipeline.scoring` first."
        )
        if close_after:
            conn.close()
        return
    urgency_scaling_point = compute_urgency_scaling_point(conn)
    print(
        f"Refreshing deterministic scores (market signal strength, source diversity, novelty momentum, urgency) for {len(rows)} opportunity space(s) -- no LLM calls, free in quota terms. Urgency scaling point: {urgency_scaling_point:.2f}\n"
    )
    for r in rows:
        signals = get_linked_signals_for_opportunity_space(conn, r["id"])
        new_deterministic = {
            "market_signal_strength": market_signal_strength(signals),
            "source_diversity": source_diversity(signals),
            "novelty_momentum": novelty_momentum(signals),
        }
        new_urgency = urgency_score(signals, scaling_point=urgency_scaling_point)
        sub_scores = {
            **new_deterministic,
            "evidence_quality": r["evidence_quality"],
            "strategic_relevance": r["strategic_relevance"],
        }
        new_total = round(sum((sub_scores[k] * WEIGHTS[k] for k in WEIGHTS)), 2)
        insert_score(
            conn,
            r["id"],
            sub_scores,
            new_total,
            evidence_quality_justification=r["evidence_quality_justification"],
            strategic_relevance_justification=r["strategic_relevance_justification"],
            urgency_score=new_urgency,
        )
        moved = " (unchanged)" if new_total == r["total_score"] else ""
        print(
            f"{r['label']} ({r['vertical']} x {r['use_case']} x {r['technology']}): total {r['total_score']}/10 -> {new_total}/10{moved}, urgency {r['urgency_score']}/10 -> {new_urgency}/10"
        )
    if close_after:
        conn.close()


def recalibrate_urgency(conn=None):
    close_after = conn is None
    if conn is None:
        conn = get_connection()
    rows = get_latest_scores(conn)
    if not rows:
        print(
            "No scored opportunity spaces found -- nothing to recalibrate. Run `python -m pipeline.scoring` first."
        )
        if close_after:
            conn.close()
        return
    urgency_scaling_point = compute_urgency_scaling_point(conn)
    print(
        f"Recalibrating urgency_score for {len(rows)} opportunity space(s) -- no LLM calls, free in quota terms. Scaling point (95th percentile): {urgency_scaling_point:.2f}\n"
    )
    for r in rows:
        signals = get_linked_signals_for_opportunity_space(conn, r["id"])
        new_urgency = urgency_score(signals, scaling_point=urgency_scaling_point)
        sub_scores = {
            "market_signal_strength": r["market_signal_strength"],
            "source_diversity": r["source_diversity"],
            "evidence_quality": r["evidence_quality"],
            "novelty_momentum": r["novelty_momentum"],
            "strategic_relevance": r["strategic_relevance"],
        }
        insert_score(
            conn,
            r["id"],
            sub_scores,
            r["total_score"],
            evidence_quality_justification=r["evidence_quality_justification"],
            strategic_relevance_justification=r["strategic_relevance_justification"],
            urgency_score=new_urgency,
        )
        print(
            f"{r['label']} ({r['vertical']} x {r['use_case']} x {r['technology']}): urgency {r['urgency_score']}/10 -> {new_urgency}/10"
        )
    if close_after:
        conn.close()


def recalibrate_right_to_win(conn=None):
    close_after = conn is None
    if conn is None:
        conn = get_connection()
    unscored_ids = {s["id"] for s in get_unscored_opportunity_spaces(conn)}
    spaces = [
        s for s in get_all_opportunity_spaces(conn) if s["id"] not in unscored_ids
    ]
    if not spaces:
        print(
            "No already-scored opportunity spaces found -- nothing to recalibrate. Run `python -m pipeline.scoring` first."
        )
        if close_after:
            conn.close()
        return
    print(
        f"Recalibrating right-to-win only for {len(spaces)} already-scored opportunity space(s) -- evidence_quality/strategic_relevance/enrichment untouched.\n"
    )
    for space in spaces:
        distance, rtw_score, assets, rtw_justification = llm_right_to_win(
            space["vertical"], space["use_case"], space["technology"]
        )
        insert_right_to_win_score(
            conn, space["id"], distance, rtw_score, assets, rtw_justification
        )
        print(
            f"{space['label']} ({space['vertical']} x {space['use_case']} x {space['technology']})"
        )
        print(
            f"  Right-to-win: {rtw_score}/10  [{distance}] assets: {assets or 'none'}"
        )
        print(f"  -> {rtw_justification}\n")
    if close_after:
        conn.close()


def recalibrate_geography(conn=None):
    close_after = conn is None
    if conn is None:
        conn = get_connection()
    unscored_ids = {s["id"] for s in get_unscored_opportunity_spaces(conn)}
    spaces = [
        s for s in get_all_opportunity_spaces(conn) if s["id"] not in unscored_ids
    ]
    if not spaces:
        print(
            "No already-scored opportunity spaces found -- nothing to re-enrich. Run `python -m pipeline.scoring` first."
        )
        if close_after:
            conn.close()
        return
    print(
        f"Re-enriching geography (new taxonomy, see config.GEOS_PROMPT) for {len(spaces)} already-scored opportunity space(s) -- attractiveness/right-to-win untouched.\n"
    )
    for space in spaces:
        signals = get_linked_signals_for_opportunity_space(conn, space["id"])
        enrichment = llm_enrich(
            space["vertical"], space["use_case"], space["technology"], signals
        )
        update_opportunity_space_enrichment(
            conn,
            space["id"],
            role=enrichment["role"],
            buyer_persona=enrichment["buyer_persona"],
            geography=enrichment["geography"],
            horizon=enrichment["horizon"],
            domain=enrichment["domain"],
            next_action_strategist=enrichment["next_action_strategist"],
            next_action_sales=enrichment["next_action_sales"],
            next_action_presales=enrichment["next_action_presales"],
        )
        print(
            f"{space['label']} ({space['vertical']} x {space['use_case']} x {space['technology']}) -> geography={enrichment['geography']}"
        )
    if close_after:
        conn.close()


def clean_scores(conn=None):
    close_after = conn is None
    if conn is None:
        conn = get_connection()
    before_scores = conn.execute("SELECT COUNT(*) AS c FROM scores").fetchone()["c"]
    before_rtw = conn.execute(
        "SELECT COUNT(*) AS c FROM right_to_win_scores"
    ).fetchone()["c"]
    conn.execute(
        "\n        DELETE FROM scores\n        WHERE id IN (\n            SELECT id FROM (\n                SELECT id, ROW_NUMBER() OVER (\n                    PARTITION BY opportunity_space_id ORDER BY computed_at DESC\n                ) AS row_number\n                FROM scores\n            )\n            WHERE row_number > 1\n        )\n        "
    )
    conn.execute(
        "\n        DELETE FROM right_to_win_scores\n        WHERE id IN (\n            SELECT id FROM (\n                SELECT id, ROW_NUMBER() OVER (\n                    PARTITION BY opportunity_space_id ORDER BY computed_at DESC\n                ) AS row_number\n                FROM right_to_win_scores\n            )\n            WHERE row_number > 1\n        )\n        "
    )
    conn.commit()
    after_scores = conn.execute("SELECT COUNT(*) AS c FROM scores").fetchone()["c"]
    after_rtw = conn.execute(
        "SELECT COUNT(*) AS c FROM right_to_win_scores"
    ).fetchone()["c"]
    print(
        f"Pruned scores: {before_scores} -> {after_scores} rows ({before_scores - after_scores} removed)."
    )
    print(
        f"Pruned right_to_win_scores: {before_rtw} -> {after_rtw} rows ({before_rtw - after_rtw} removed)."
    )
    if close_after:
        conn.close()


def rescue_fallback_scores(conn=None):
    close_after = conn is None
    if conn is None:
        conn = get_connection()
    spaces = get_opportunity_spaces_with_fallback_scores(conn)
    if not spaces:
        print(
            "No opportunity space currently has a fallback score -- nothing to rescue."
        )
        if close_after:
            conn.close()
        return
    print(
        f"Rescuing {len(spaces)} opportunity space(s) that got a neutral fallback score (Groq quota was exhausted when they were first scored):\n"
    )
    for space in spaces:
        sub_scores, total, urgency = score_opportunity_space(conn, space)
        distance, rtw_score, assets, rtw_justification = llm_right_to_win(
            space["vertical"], space["use_case"], space["technology"]
        )
        insert_right_to_win_score(
            conn, space["id"], distance, rtw_score, assets, rtw_justification
        )
        print(
            f"{space['label']} ({space['vertical']} x {space['use_case']} x {space['technology']})"
        )
        print(f"  Attractiveness: {total}/10  {sub_scores}")
        print(f"  Right-to-win:   {rtw_score}/10  [{distance}]")
        print(f"  -> {rtw_justification}\n")
        vertical = space["vertical"]
        signals = get_linked_signals_for_opportunity_space(conn, space["id"])
        enrichment = llm_enrich(
            vertical, space["use_case"], space["technology"], signals
        )
        update_opportunity_space_enrichment(
            conn,
            space["id"],
            role=enrichment["role"],
            buyer_persona=enrichment["buyer_persona"],
            geography=enrichment["geography"],
            horizon=enrichment["horizon"],
            domain=enrichment["domain"],
            next_action_strategist=enrichment["next_action_strategist"],
            next_action_sales=enrichment["next_action_sales"],
            next_action_presales=enrichment["next_action_presales"],
        )
    if close_after:
        conn.close()


if __name__ == "__main__":
    from_label = None
    to_label = None
    for arg in sys.argv:
        if arg.startswith("--from="):
            from_label = arg.split("=", 1)[1]
        elif arg.startswith("--to="):
            to_label = arg.split("=", 1)[1]
    if "--refresh" in sys.argv:
        recalibrate_deterministic_scores()
    elif "--recalibrate-urgency" in sys.argv:
        recalibrate_urgency()
    elif "--recalibrate-right-to-win" in sys.argv:
        recalibrate_right_to_win()
    elif "--recalibrate-geography" in sys.argv:
        recalibrate_geography()
    elif "--rescue-fallback" in sys.argv:
        rescue_fallback_scores()
    elif "--prune-scores" in sys.argv:
        clean_scores()
    else:
        score_all_opportunity_spaces(
            force="--force" in sys.argv, from_label=from_label, to_label=to_label
        )