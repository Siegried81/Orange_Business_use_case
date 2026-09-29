import os
import json
from enum import Enum
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAXONOMY_EXTENSIONS_PATH = os.path.join(BASE_DIR, "taxonomy_extensions.json")


def _init_taxonomy_extensions():
    if not os.path.exists(TAXONOMY_EXTENSIONS_PATH):
        with open(TAXONOMY_EXTENSIONS_PATH, "w") as f:
            json.dump([], f)


_init_taxonomy_extensions()


def _load_taxonomy_extensions():
    if os.path.exists(TAXONOMY_EXTENSIONS_PATH):
        with open(TAXONOMY_EXTENSIONS_PATH, "r") as f:
            data = json.load(f)
        use_cases = [item["term"] for item in data if item["category"] == "use_case"]
        technologies = [
            item["term"] for item in data if item["category"] == "technology"
        ]
        return (use_cases, technologies)
    return ([], [])


_EXT_USE_CASES, _EXT_TECHNOLOGIES = _load_taxonomy_extensions()
load_dotenv()
DB_PATH = "radar.db"
NEWSAPI_AI_KEY = os.environ.get("NEWSAPI_AI_KEY", "")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
ROLES = ["Strategist", "Sales", "Presales"]
BUYER_PERSONAS = [
    "CIOs",
    "IT and network executives",
    "Security executives",
    "COOs & production executives",
    "CMOs & CX executives",
    "CISOs",
    "CDOs",
    "Industrial safety managers",
    "Quality managers",
]
GEOS = [
    "Benelux",
    "Germany",
    "Southern Europe",
    "DACH",
    "UK & Ireland",
    "Nordics",
    "Eastern Europe",
    "Africa",
    "Middle East",
    "Asia Pacific",
    "Americas",
]
GEOS_PROMPT = "Benelux (Netherlands, Belgium, Luxembourg), Germany (Germany), Southern Europe (Italy, Spain, Portugal, Israel), DACH (Switzerland, Austria), UK & Ireland (United Kingdom, Ireland), Nordics (Norway, Sweden, Denmark, Finland, Iceland), Eastern Europe, Africa, Middle East, Asia Pacific, Americas"
HORIZONS = ["Now", "Next", "Later"]
VERTICAL_SEEDS = {
    "Manufacturing": "private 5G edge AI manufacturing safety",
    "Finance & Insurance": "agentic AI insurance claims automation",
    "Public Sector": "sovereign cloud EU government data",
    "Retail": "retail contact centre automation agentic AI",
    "Healthcare": "AI clinical workflow hospital data platform",
    "Energy": "AI grid optimization renewable energy IoT",
    "Transportation and Logistics": "AI fleet tracking supply chain IoT",
    "Defense": "defense zero trust secure communications sovereign networks",
    "Automotive": "connected vehicle V2X 5G manufacturing cloud platform",
    "Construction": "IoT connected jobsite digital twin construction safety",
    "Life Sciences": "cloud data platform pharma clinical trial AI compliance",
    "Wholesale": "supply chain visibility IoT wholesale distribution cloud",
    "Media & Entertainment": "cloud content delivery streaming security AI",
    "Natural Resources": "IoT remote monitoring mining oil gas edge AI",
    "Aerospace & Defense": "aerospace defense manufacturing secure cloud cybersecurity",
    "Fast Moving Consumer Goods": "AI demand forecasting supply chain FMCG cloud",
    "IT and Services": "managed IT services cloud modernization cybersecurity",
}
VERTICALS = sorted(VERTICAL_SEEDS)
TRUST_CRITICAL_VERTICALS = {"Defense", "Healthcare", "Aerospace & Defense"}
SIGNAL_TYPES = [
    "trend",
    "regulation",
    "buying_signal",
    "market_move",
    "tech_maturity",
    "proof_signal",
]
USE_CASES_TAXONOMY = list(
    dict.fromkeys(
        [
            "Energy Optimization",
            "Demand Forecasting",
            "IT Operations Automation",
            "Imaging Analytics",
            "Network Modernization & SD-WAN",
            "Cloud Infrastructure Modernization",
            "Cyber Defense & Zero Trust",
            "Customer Experience",
            "Employee Experience",
            "Operational Excellence",
            "Digital Infrastructure",
            "Data Sovereignty",
            "Cybersecurity",
            "Contact Centre Automation",
            "Clinical Workflow Automation",
            "Predictive Maintenance",
            "Supply Chain Visibility",
            "Grid Optimization",
            "Industrial Digital Twin & Automation",
            "Citizen Participation Platforms",
            "Manufacturing Process Automation",
            "Infrastructure Planning & Management",
            "Post-Quantum Cryptography Testing Infrastructure",
            "Strategic Communications & Advertising Consultancy",
        ]
        + _EXT_USE_CASES
    )
)
TECHNOLOGIES_TAXONOMY = list(
    dict.fromkeys(
        [
            "Cloud Data Platform",
            "IoT Platforms",
            "Computer Vision",
            "Machine Learning",
            "Generative AI",
            "Network & SD-WAN",
            "Cloud",
            "Cybersecurity",
            "5G",
            "IoT",
            "AI, Data, Cloud",
            "Agentic AI",
            "Edge Computing",
            "Digital Twins",
            "Quantum-safe Cryptography",
        ]
        + _EXT_TECHNOLOGIES
    )
)
DOMAINS_TAXONOMY = [
    {"code": "ox", "name": "Smart Industries"},
    {"code": "conn", "name": "Connectivity Solutions"},
    {"code": "cyber", "name": "Cybersecurity"},
    {"code": "cloud", "name": "Cloud"},
    {"code": "cx", "name": "Customer Experience"},
    {"code": "ex", "name": "Employee Experience"},
]
PORTFOLIO_DISTANCE = {
    "L0": {
        "label": "Direct offer",
        "blurb": "An existing Orange Business offer addresses this as-is.",
    },
    "L1": {
        "label": "Bundle",
        "blurb": "Two or more existing offers exist but are not yet packaged together.",
    },
    "L2": {
        "label": "Partner-dependent",
        "blurb": "Needs a capability held by an existing partner, not Orange itself.",
    },
    "L3": {
        "label": "Adjacent",
        "blurb": "Needs one capability to be built or acquired -- close, but not there yet.",
    },
    "L4": {
        "label": "White space",
        "blurb": "No plausible path from the current portfolio.",
    },
}
RECURRING_THEME_PROMOTION_THRESHOLD = 2
GOOGLE_NEWS_QUERIES = [{"vertical": v, "query": q} for v, q in VERTICAL_SEEDS.items()]
ENABLE_GDELT = True
_GDELT_SAFE_SUBSTITUTIONS = {"AI": "artificial intelligence", "EU": "European Union"}


def _gdelt_safe_query(seed):
    words = seed.split()
    return " ".join((_GDELT_SAFE_SUBSTITUTIONS.get(w, w) for w in words))


GDELT_QUERIES = [
    {"vertical": v, "query": _gdelt_safe_query(q)} for v, q in VERTICAL_SEEDS.items()
]
ARXIV_QUERIES = GOOGLE_NEWS_QUERIES
SEMANTIC_SCHOLAR_QUERIES = ARXIV_QUERIES
COMPETITORS = [
    "NTT",
    "AT&T Business",
    "Vodafone Business",
    "BT Business",
    "Deutsche Telekom",
    "Colt Technology",
    "Verizon Business",
]
COMPETITOR_QUERIES = [
    {"vertical": v, "query": f"({' OR '.join(COMPETITORS)}) {q}"}
    for v, q in VERTICAL_SEEDS.items()
]
REGULATION_QUERIES = [
    {"vertical": v, "query": f"site:eur-lex.europa.eu {v} regulation"}
    for v in VERTICALS
]
BUYING_SIGNAL_QUERIES = [
    {"vertical": v, "query": f"site:ted.europa.eu {v} tender"} for v in VERTICALS
]
ENABLE_TED = True
TED_API_URL = "https://api.ted.europa.eu/v3/notices/search"
TED_LOOKBACK_DAYS = 730
TED_SLEEP_SECONDS = 2
TED_FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "publication-date",
    "deadline-receipt-tender-date-lot",
    "classification-cpv",
]
TED_QUERIES = [
    {"vertical": v, "query": f'FT~"{seed}"'} for v, seed in VERTICAL_SEEDS.items()
]
ENABLE_NEWSAPI_AI = True
NEWSAPI_AI_URL = "https://eventregistry.org/api/v1/article/getArticles"
NEWSAPI_AI_SLEEP_SECONDS = 1
NEWSAPI_AI_QUERIES = [{"vertical": v, "query": q} for v, q in VERTICAL_SEEDS.items()]
VENDOR_FEEDS = [
    {"name": "AWS News Blog", "url": "https://aws.amazon.com/blogs/aws/feed/"},
    {
        "name": "Microsoft Azure Blog",
        "url": "https://azure.microsoft.com/en-us/blog/feed/",
    },
    {"name": "NVIDIA Blog", "url": "https://blogs.nvidia.com/feed/"},
    {"name": "Cisco Blog", "url": "https://blogs.cisco.com/feed"},
]
HN_QUERIES = [
    "private 5G",
    "agentic AI insurance",
    "sovereign cloud",
    "AI contact center",
    "grid AI",
]
GDELT_SLEEP_SECONDS = 75
GDELT_RETRY_WAIT_SECONDS = 15
GDELT_COOLDOWN_MINUTES = 20
SEMANTIC_SCHOLAR_SLEEP_SECONDS = 20
SEMANTIC_SCHOLAR_RETRY_WAIT_SECONDS = 15
SEMANTIC_SCHOLAR_COOLDOWN_MINUTES = 20
ARXIV_SLEEP_SECONDS = 3
GOOGLE_NEWS_SLEEP_SECONDS = 1
ORANGE_BUSINESS_ASSETS = [
    {"name": "API Satellite", "category": "Relation client"},
    {"name": "API Mobile Suite", "category": "Mobilite"},
    {"name": "API M2M for IoT Connect Express", "category": "Data, IA & IoT"},
    {"name": "API Contact Everyone", "category": "Collaboration & Teletravail"},
    {"name": "API Cloud Avenue", "category": "Cloud"},
    {"name": "API Live Identity Captcha", "category": "Securite"},
    {"name": "API Live Identity Verify", "category": "Securite"},
    {"name": "API Flexible SDWAN Cisco", "category": "Internet & Reseaux"},
    {"name": "API Business Talk Digital", "category": "Telephonie fixe & Voix"},
    {"name": "API Evolution Platform", "category": "Relation client"},
    {"name": "API Incident", "category": "Securite"},
    {"name": "API View Bill", "category": "Relation client"},
    {"name": "API Maintenance", "category": "Relation client"},
    {"name": "API Mobile", "category": "Relation client"},
    {"name": "API Ordering et Order Tracking", "category": "Relation client"},
    {"name": "API Eligibility", "category": "Internet & Reseaux"},
    {"name": "API Core Information", "category": "Relation client"},
]
ANALYST_RECOGNITION = [
    {
        "fact": "Orange Business recognized as a Leader for the 23rd consecutive year in the Gartner Magic Quadrant 2026 for Global WAN Services",
        "source": "orange-business.com/en/about-us/analysts/gartner-recognition-for-global-wan-services",
    },
    {
        "fact": "Orange Business holds a 4.4/5 overall rating on Gartner Peer Insights (16 verified reviews, Unified Communications as a Service market)",
        "source": "gartner.com/reviews/market/unified-communications-as-a-service/vendor/orange-business",
    },
    {
        "fact": "Orange Business named a Leader in the Gartner Magic Quadrant 2026 for 4G and 5G Private Mobile Network Services",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "fact": "Orange Business rated a Leader in GlobalData's 2025 Company Assessment for Global Enterprise",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "fact": "158 analyst mentions and 56 Leader-tier reports in 2025",
        "source": "Orange Business corporate presentation, 2026",
    },
]
CUSTOMER_REFERENCES = [
    {
        "customer": "Saint-Gobain Glass",
        "vertical": "Manufacturing",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/saint-gobain-glass",
    },
    {
        "customer": "De Lijn",
        "vertical": "Public Sector",
        "source": "https://www.orange-business.com/be-en/about-us/customer-stories/lijn-data-visualization-enhances-public-transport-service",
    },
    {
        "customer": "SPF Finances (Belgian Federal Public Service Finance)",
        "vertical": "Public Sector",
        "source": "https://www.orange-business.com/be-en/about-us/customer-stories/dynamic-webshop-belgian-federal-public-service-finance",
    },
    {
        "customer": "DINUM (French Inter-ministerial Directorate for Digital Affairs)",
        "vertical": "Public Sector",
        "source": "https://www.orange-business.com/en/case-study/simplifying-data-network-management-using-apis",
    },
    {
        "customer": "BNP Paribas",
        "vertical": "Finance & Insurance",
        "source": "https://www.orange-business.com/en/press/bnp-paribas-joins-forces-orange-business-services-deploy-sd-wan-1800-retail-sites-france",
    },
    {
        "customer": "Groupama",
        "vertical": "Finance & Insurance",
        "source": "https://www.orange-business.com/en/case-study/groupama-designs-digital-and-mobile-journey",
    },
    {
        "customer": "Tricots Saint James",
        "vertical": "Manufacturing",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/tricots-saint-james-entrusting-all-its-it-management-orange-business",
    },
    {
        "customer": "MicroPort CardioFlow",
        "vertical": "Healthcare",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/microport-cardioflow-enabling-reliable-remote-heart-monitoring-through",
    },
    {
        "customer": "Boulanger",
        "vertical": "Retail",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/ai-conversational-agent-customer-service-expertime-helped-boulanger-move",
    },
    {
        "customer": "Banqsoft",
        "vertical": "Finance & Insurance",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/foundation-innovation-how-banqsoft-builds-european-cloud-platform",
    },
    {
        "customer": "Stø",
        "vertical": "Finance & Insurance",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/sto-sovereign-services-banking-finance",
    },
    {
        "customer": "Skytale",
        "vertical": "Public Sector",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/skytale-secure-communication-platform-delivered-record-time",
    },
    {
        "customer": "Grand Paris Sud (arenas)",
        "vertical": "Public Sector",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/grand-paris-sud-arenas-secure-indoor-europe-esports-hub",
    },
    {
        "customer": "Aberg Connect",
        "vertical": "Automotive",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/aberg-connect-launches-europe-wide-tire-pressure-monitoring-system",
    },
    {
        "customer": "Toyota",
        "vertical": "Automotive",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/toyota-gets-connected-cars-right-lane-identify-traffic-hazard-spots",
    },
    {
        "customer": "Cainiao (Alibaba logistics arm)",
        "vertical": "Transportation and Logistics",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/cainiao-partners-prioritize-customer-privacy-improve-trust",
    },
    {
        "customer": "Intis (unattended/autonomous retail)",
        "vertical": "Retail",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/intis-connects-unattended-retail-around-world-televend",
    },
    {
        "customer": "TMF Group",
        "vertical": "IT and Services",
        "source": "https://www.orange-business.com/en/about-us/customer-stories/tmf-group-reduces-risk-enhances-services-hybrid-cloud",
    },
]
OPPORTUNITY_COUNT_BY_VERTICAL = {}
PIPELINE_VALUE_BY_VERTICAL = {}
CAPABILITY_STATS = [
    {
        "stat": "30,000 employees",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "stat": "EUR 7.3bn revenue (2025)",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "stat": "40,000+ B2B customers, 200+ countries",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "stat": "70+ data centers across 5 continents",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "stat": "18 SOCs and 15 CyberSOCs worldwide",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "stat": "Cyberdefense revenue grew 6.8% in 2025",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "stat": "250+ dedicated Defense experts",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "stat": "1,000+ dedicated Healthcare experts",
        "source": "Orange Business corporate presentation, 2026",
    },
    {
        "stat": "Tier-1 global backbone present in 200+ countries and territories",
        "source": "orange-business.com/en/about-us/analysts/gartner-recognition-for-global-wan-services",
    },
    {
        "stat": "Evolution Platform (Network as a Service): SD-WAN, SASE and cloud connectivity combined through a single API/portal with on-demand, SLA-backed delivery",
        "source": "orange-business.com/en/about-us/analysts/gartner-recognition-for-global-wan-services",
    },
    {
        "stat": "5,500+ AI, Data and Cloud experts",
        "source": "orange-business.com homepage",
    },
    {
        "stat": "#1 global voice and data network; team presence in 65 countries on all continents; 6 major Service Centers",
        "source": "orange-business.com homepage",
    },
]
PARTNER_TIERS = {
    "Cisco": "Global Gold Partner",
    "AWS": "Advanced Partner, MSP",
    "Palo Alto Networks": "Diamond, #1 EMEA Partner",
    "Microsoft": "Gold (MAICPP), Partner of the Year",
    "Google Cloud": "Premier",
}


class ValueProposition(str, Enum):
    SECURE_CONNECTIVITY = "NextGen Secured Connectivity"
    SECURE_CLOUD = "Secure Cloud Orchestration"
    GENAI_WORKFORCE = "GenAI Empowered Workforce"
    CUSTOMER_EXPERIENCE = "Orchestrate Customer Interactions"
    OPERATIONAL_EXPERIENCE = "Smart Manufacturing & Operations"


VALUE_PROP_KEYWORDS = {
    ValueProposition.SECURE_CONNECTIVITY: [
        "sd-wan",
        "network",
        "5g",
        "connectivity",
        "sase",
    ],
    ValueProposition.SECURE_CLOUD: [
        "cloud migration",
        "multi-cloud",
        "sovereign cloud",
        "cloud security",
        "cloud",
    ],
    ValueProposition.GENAI_WORKFORCE: [
        "copilot",
        "generative ai",
        "agentic ai",
        "productivity",
        "collaboration",
    ],
    ValueProposition.CUSTOMER_EXPERIENCE: [
        "contact center",
        "contact centre",
        "customer journey",
        "cx",
        "personalization",
    ],
    ValueProposition.OPERATIONAL_EXPERIENCE: [
        "iot",
        "predictive maintenance",
        "computer vision",
        "ot",
        "manufacturing",
    ],
}


def map_to_value_proposition(os_text):
    text = os_text.lower()
    for vp, keywords in VALUE_PROP_KEYWORDS.items():
        if any((kw in text for kw in keywords)):
            return vp
    return None


CANDIDATES = [
    (
        "OS001",
        "Public Sector",
        "Sovereign citizen data hosting",
        "Sovereign cloud + GPU inference",
    ),
    (
        "OS002",
        "Manufacturing",
        "Fire and hazard detection",
        "Edge computer vision (Raspberry Pi class)",
    ),
    (
        "OS003",
        "Finance & Insurance",
        "Conduct-risk / compliance monitoring",
        "AI surveillance of communications",
    ),
    (
        "OS004",
        "Manufacturing",
        "Remote-controlled industrial robots",
        "Vision-guided teleoperation",
    ),
    ("OS005", "Manufacturing", "Energy Optimization", "IoT Platforms"),
    ("OS006", "Manufacturing", "Operational Excellence", "Machine Learning"),
    ("OS007", "Manufacturing", "Cyber Defense & Zero Trust", "Cybersecurity"),
    ("OS008", "Manufacturing", "Imaging Analytics", "Computer Vision"),
    ("OS009", "Finance & Insurance", "Cloud Infrastructure Modernization", "Cloud"),
    ("OS010", "Finance & Insurance", "Cybersecurity", "Machine Learning"),
    ("OS011", "Finance & Insurance", "Customer Experience", "Generative AI"),
    ("OS012", "Finance & Insurance", "IT Operations Automation", "Machine Learning"),
    ("OS013", "Public Sector", "Data Sovereignty", "Cloud"),
    ("OS014", "Public Sector", "Cyber Defense & Zero Trust", "Cybersecurity"),
    ("OS015", "Public Sector", "Digital Infrastructure", "IoT Platforms"),
    ("OS040", "Healthcare", "Clinical Workflow Automation", "Machine Learning"),
    ("OS041", "Energy", "Employee Experience", "Generative AI"),
    ("OS042", "Defense", "Cyber Defense & Zero Trust", "Cybersecurity"),
    ("OS043", "Automotive", "Predictive Maintenance", "IoT Platforms"),
    ("OS044", "Construction", "Industrial Digital Twin & Automation", "Digital Twins"),
    ("OS045", "Life Sciences", "Data Sovereignty", "Cloud"),
    ("OS046", "Wholesale", "Supply Chain Visibility", "IoT Platforms"),
    ("OS047", "Media & Entertainment", "Cyber Defense & Zero Trust", "Cybersecurity"),
    ("OS048", "Natural Resources", "Operational Excellence", "Computer Vision"),
    ("OS049", "Aerospace & Defense", "Cyber Defense & Zero Trust", "Cybersecurity"),
    ("OS050", "Fast Moving Consumer Goods", "Demand Forecasting", "Machine Learning"),
    ("OS051", "IT and Services", "Cloud Infrastructure Modernization", "Cloud"),
]

# One threshold (0-10) for both axes of the Attractiveness x Right-to-win
# quadrant. The dashboard's overview counts and its per-opportunity detail
# panel both go through quadrant() below, so they cannot disagree.
STRONG_THRESHOLD = 7

QUADRANTS = ("strong", "needs_capability", "moderate_market", "low_both")


def quadrant(attractiveness, right_to_win, threshold=STRONG_THRESHOLD):
    """Return the QUADRANTS key for one opportunity space, or None if either
    score is missing.

    Every (attractiveness, right_to_win) pair lands in exactly one quadrant:
    each axis is split once at the same threshold (>= is "high"). Missing is
    not zero -- an unscored axis returns None instead of counting as "low".
    """
    if attractiveness is None or right_to_win is None:
        return None
    if attractiveness != attractiveness or right_to_win != right_to_win:  # NaN
        return None
    high_attr = attractiveness >= threshold
    high_rtw = right_to_win >= threshold
    if high_attr and high_rtw:
        return "strong"
    if high_attr:
        return "needs_capability"
    if high_rtw:
        return "moderate_market"
    return "low_both"
