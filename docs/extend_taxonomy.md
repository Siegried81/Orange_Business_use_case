# Extend Taxonomy
As we will go on in the project, it will evolve to an **Extended Taxonomy** that can update itself also considering signals found during exploration phase:

* it will be enriched by signals.
* new entries are added when a theme recurs with sufficient frequency.
* the new entries are submitted to the team for validation.

Logic example:

```python
# `taxonomy_manager.py` organization idea

class TaxonomyManager:
    def __init__(self):
        self.verticals = self.load_core_verticals()
        self.use_cases = self.load_core_use_cases()
        self.technologies = self.load_core_technologies()
        self.pending_approvals = []  # New entries to be validated
    
    def suggest_new_term(self, category, term, frequency):
        """
        Proposes a new taxonomy entry.
        category: 'vertical', 'use_case', 'technology'
        term: the new keyword
        frequency: how many times it appeared in the signals
        """
        if frequency > 10:  # Materiality threshold
            self.pending_approvals.append({
                'category': category,
                'term': term,
                'frequency': frequency,
                'status': 'pending'
            })
    
    def approve_term(self, term):
        """
        Approves a new entry, moving it to the active taxonomy.
        """
        # Logic for moving from pending to active
        pass
```

---

✨ **Use a dictionary** for internal organization, but you can **extract the lists** when needed.

```python
# taxonomy.py

TAXONOMY = {
    "verticals": [
        "Manufacturing",
        "Retail",
        "Finance",
        "Public Sector",
        "Healthcare",
        "Defense",
        "Automotive",
        "Fast Moving Consumer Goods",
        "Industry",
        "IT and Services"
    ],
    "use_cases": [
        "Energy Optimization",
        "Demand Forecasting",
        "IT Operations Automation",
        "Imaging Analytics",
        "Network Modernization & SD-WAN",
        "Cloud Infrastructure Modernization",
        "Cyber Defence & Zero Trust",
        "Customer Experience",
        "Employee Experience",
        "Operational Excellence",
        "Digital Infrastructure",
        "Data Sovereignty",
        "Cybersecurity"
    ],
    "technologies": [
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
        "AI"
    ],
    # Optional metadata for traceability
    "metadata": {
        "verticals": {"source": "innovation_radar_summary_roadmap_additions, page 6", "last_updated": "2026-08-18"},
        "use_cases": {"source": "Use Case presentation, page 11", "last_updated": "2026-08-18"},
        "technologies": {"source": "Use Case presentation, page 11", "last_updated": "2026-08-18"}
    }
}

# Utility for extracting lists when needed
def get_verticals():
    return TAXONOMY["verticals"]

def get_use_cases():
    return TAXONOMY["use_cases"]

def get_technologies():
    return TAXONOMY["technologies"]

def get_all_categories():
    return TAXONOMY["verticals"] + TAXONOMY["use_cases"] + TAXONOMY["technologies"]
```


##### ✨after MVP: 
To have the **date updated automatically** every time the taxonomy evolves, use `datetime`.

```python
# taxonomy.py

import json
from datetime import datetime
import os

# Definizione della tassonomia
CORE_VERTICALS = [
    "Manufacturing",
    "Retail",
    "Finance",
    "Public Sector",
    "Healthcare",
    "Defense",
    "Automotive",
    "Fast Moving Consumer Goods",
    "Industry",
    "IT and Services"
]

CORE_USE_CASES = [
    "Energy Optimization",
    "Demand Forecasting",
    "IT Operations Automation",
    "Imaging Analytics",
    "Network Modernization & SD-WAN",
    "Cloud Infrastructure Modernization",
    "Cyber Defence & Zero Trust",
    "Customer Experience",
    "Employee Experience",
    "Operational Excellence",
    "Digital Infrastructure",
    "Data Sovereignty",
    "Cybersecurity"
]

CORE_TECHNOLOGIES = [
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
    "AI"
]

# Funzione per salvare la tassonomia con timestamp
def save_taxonomy(verticals, use_cases, technologies, source_info):
    """
    Salva la tassonomia in un file JSON con la data di ultima modifica.
    """
    taxonomy = {
        "verticals": verticals,
        "use_cases": use_cases,
        "technologies": technologies,
        "metadata": {
            "verticals": {
                "source": source_info.get("verticals", "Unknown"),
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            "use_cases": {
                "source": source_info.get("use_cases", "Unknown"),
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            "use_cases": {
                "source": source_info.get("technologies", "Unknown"),
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
    }
    
    with open("taxonomy.json", "w") as f:
        json.dump(taxonomy, f, indent=2)
    
    print(f"✅ Taxonomy saved with timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Funzione per caricare la tassonomia
def load_taxonomy():
    """
    Carica la tassonomia dal file JSON.
    """
    if os.path.exists("taxonomy.json"):
        with open("taxonomy.json", "r") as f:
            return json.load(f)
    else:
        # Se il file non esiste, crea la tassonomia iniziale
        source_info = {
            "verticals": "innovation_radar_summary_roadmap_additions, page 6",
            "use_cases": "Use Case presentation, page 11",
            "technologies": "Use Case presentation, page 11"
        }
        save_taxonomy(CORE_VERTICALS, CORE_USE_CASES, CORE_TECHNOLOGIES, source_info)
        return load_taxonomy()

# ============================================
# USO NEL CODICE
# ============================================

# Carica la tassonomia (con timestamp)
taxonomy = load_taxonomy()

# Accedi alle liste
verticals = taxonomy["verticals"]
use_cases = taxonomy["use_cases"]
technologies = taxonomy["technologies"]

# Accedi ai metadati
last_updated = taxonomy["metadata"]["verticals"]["last_updated"]
print(f"📅 Last updated: {last_updated}")
```