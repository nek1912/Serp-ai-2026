"""
Deterministic query classification for the Web RAG pipeline.

This module does NOT generate answers.

It determines:
- domain
- jurisdiction
- state
- intent

Supported domains:
- cooperative
- pacs
- schemes
- pmfby
- agriculture
- finlit / financial_inclusion
- grievance
- driving_licence

Supported intents:
- INFORMATIONAL
- ELIGIBILITY
- APPLICATION
- REGISTRATION
- DOCUMENT_REQUIREMENTS
- BENEFIT
- SUBSIDY_AMOUNT
- DEADLINE
- STATUS
- GRIEVANCE
- CONTACT
- SERVICE_ACCESS
- LOCATION
- COMPARISON
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class QueryClassification:
    domain: str
    jurisdiction: str
    state: Optional[str]
    intent: str
    confidence: float


DOMAIN_KEYWORDS = {
    "pmfby": [
        "pmfby",
        "fasal bima",
        "crop insurance",
        "pradhan mantri fasal",
        "crop insurance scheme",
        "crop damage",
        "crop loss",
        "insurance claim",
        "premium subsidy",
        "wbcis",
        "पीएमएफबीवाई",
        "फसल बीमा",
        "प्रधानमंत्री फसल",
        "फसल नुकसान",
        "बीमा दावा",
        "વીમા",
        "ફસલ બીમા",
        "પીએમએફબીવાઈ",
        "પાક વીમો",
        "પાક નુકસાન",
        "વીમા દાવો",
        "પ્રધાનમંત્રી ફસલ બીમા યોજના",
        "पीक विमा",
        "पंतप्रधान पीक विमा योजना",
        "ফসল বীমা",
        "প্রধানমন্ত্রী ফসল বীমা",
        "பயிர் காப்பீடு",
    ],
    "pacs": [
        "pacs",
        "primary agricultural credit society",
        "primary agriculture credit society",
        "credit cooperative",
        "cooperative credit",
        "cooperative training",
        "sahakari talim",
        "sahakari bank",
        "pacs project",
        "pacspl",
        "प्राथमिक कृषि ऋण सहकारी समिति",
        "प्राथमिक कृषि साख समिति",
        "सहकारी प्रशिक्षण",
        "सहकारी बैंक",
        "सहकारी ऋण",
        "સહકારી તાલીમ",
        "સહકારી મંડળી",
        "સહકારી બેંક",
        "પ્રાથમિક કૃષિ ધિરાણ મંડળી",
        "સહકારી સંસ્થા",
        "सहकारी संस्था",
        "प्राथमिक कृषी पतसंस्था",
        "সমবায় সমিতি",
        "কৃষি ঋণ সমিতি",
        "கூட்டுறவு சங்கம்",
    ],
    "pacs_computerization": [
        "computerization",
        "digitization",
        "digital pacs",
        "pacspl",
        "ncip",
        "ict",
        "erp",
        "pmu",
        "micro-atm",
        "core banking",
        "pacs project",
        "data readiness",
    ],
    "cooperative": [
        "cooperative",
        "co-operative",
        "cooperative society",
        "cooperative law",
        "bylaws",
        "by-laws",
        "member rights",
        "society registration",
        "cooperative training",
        "sahakari",
        "सहकारी",
        "समिति",
        "સહકારી",
        "સમવાય",
        "கூட்டுறவு",
        "সমবায়",
    ],
    "schemes": [
        "scheme",
        "yojana",
        "ministry of cooperation",
        "government scheme",
        "subsidy",
        "benefit",
        "eligibility",
        "apply for",
        "apply to",
        "applicant",
        "beneficiary",
        "government program",
        "government programme",
        "portal",
        "registration for",
        "योजना",
        "सरकारी योजना",
        "યોજના",
        "સરકારી યોજના",
        "સબસિડી",
        "લાભ",
        "પાત્રતા",
        "शासकीय योजना",
        "প্রকল্প",
        "সরকারি প্রকল্প",
        "திட்டம்",
        "அரசு திட்டம்",
    ],
    "agriculture": [
        "farmer",
        "farmers",
        "farm",
        "farming",
        "agriculture",
        "agricultural",
        "crop",
        "crops",
        "cultivation",
        "harvest",
        "fertilizer",
        "pesticide",
        "irrigation",
        "seed",
        "tractor",
        "mandi",
        "apmc",
        "msp",
        "kisan",
        "khedut",
        "किसान",
        "खेती",
        "फसल",
        "सिंचाई",
        "उर्वरक",
        "बीज",
        "ખેતી",
        "ખેડૂત",
        "પાક",
        "કાપાસ",
        "ફસલ",
        "ખાતર",
        "સિંચાઈ",
        "બીજ",
        "શેતી",
        "શતકરી",
        "কৃষি",
        "কৃষক",
        "চাষ",
        "விவசாயம்",
        "விவசாயி",
    ],
    "finlit": [
        "financial literacy",
        "loan",
        "credit",
        "interest",
        "banking",
        "savings",
        "financial",
        "account",
        "jan dhan",
        "pmjdy",
        "mudra",
        "microfinance",
        "self help group",
        "shg",
        "rupay",
        "kcc",
        "kisan credit card",
        "वित्तीय साक्षरता",
        "बैंक खाता",
        "जन धन",
        "मुद्रा ऋण",
        "ऋण",
        "बैंकिंग",
        "નાણાકીય સમાવેશ",
        "બેંક ખાતું",
        "જન ધન",
        "મુદ્રા લોન",
        "ஆર્થિક સમાવેશ",
        "ব্যাংক অ্যাকাউন্ট",
        "நிதி உள்ளடக்கம்",
    ],
    "financial_inclusion": [
        "financial inclusion",
        "jan dhan",
        "pmjdy",
        "mudra loan",
        "microfinance",
        "self help group",
        "shg",
        "bank account",
    ],
    "grievance": [
        "grievance",
        "complaint",
        "complain",
        "appeal",
        "dispute",
        "redressal",
        "redress",
        "cpgrams",
        "rti",
        "right to information",
        "ombudsman",
        "शिकायत",
        "आरटीआई",
        "सूचना का अधिकार",
        "लोक शिकायत",
        "ફરિયાદ",
        "આરટીઆઈ",
        "માહિતી અધિકાર",
        "જાહેર ફરિયાદ",
        "તક્રાર",
        "माहिती अधिकार",
        "অভিযোগ",
        "তথ্য অধিকার",
        "புகார்",
        "தகவல் உரிமை",
    ],
    "driving_licence": [
        "driving licence",
        "driving license",
        "learner licence",
        "learner license",
        "learner's licence",
        "learner's license",
        "driving licence application",
        "rto",
        "motor vehicle",
        "driving test",
        "parivahan",
        "sarathi",
        "ड्राइविंग लाइसेंस",
        "लर्नर परमिट",
        "आरटीओ",
        "वाहन पंजीकरण",
        "ડ્રાઇવિંગ લાઇસન્સ",
        "લર્નર પરમિટ",
        "આરટીઓ",
        "વાહન નોંધણી",
        "वाहन चालक परवाना",
        "ড্রাইভিং লাইসেন্স",
        "ஓட்டுநர் உரிமம்",
    ],
}


INTENT_KEYWORDS = {
    "INFORMATIONAL": [
        "what is",
        "who is",
        "history",
        "overview",
        "about",
        "definition",
        "explain",
        "details",
        "information",
        "tell me about",
    ],
    "ELIGIBILITY": [
        "eligible",
        "eligibility",
        "qualify",
        "qualification",
        "who can",
        "requirements for",
        "criteria",
        "પાત્રતા",
        "पात्रता",
    ],
    "APPLICATION": [
        "apply",
        "application",
        "how to apply",
        "application process",
        "fill out",
        "submit application",
        "અરજી",
        "आवेदन",
    ],
    "REGISTRATION": [
        "register",
        "registration",
        "how to register",
        "sign up",
        "enroll",
        "enrollment",
        "નોંધણી",
        "पंजीकरण",
    ],
    "DOCUMENT_REQUIREMENTS": [
        "documents required",
        "what documents",
        "paperwork",
        "forms needed",
        "required documents",
        "દસ્તાવેજ",
        "दस्तावेज",
    ],
    "BENEFIT": [
        "benefit",
        "benefits",
        "what do i get",
        "advantages",
        "help",
        "assistance",
        "લાભ",
        "लाभ",
    ],
    "SUBSIDY_AMOUNT": [
        "subsidy amount",
        "how much",
        "amount",
        "value",
        "sum",
        "financial aid",
        "money",
        "સબસિડી",
        "सब्सिडी",
    ],
    "DEADLINE": [
        "deadline",
        "last date",
        "due date",
        "when to apply",
        "closing date",
        "time limit",
    ],
    "STATUS": [
        "status",
        "track",
        "check status",
        "application status",
        "progress",
        "સ્થિતિ",
        "स्थिति",
    ],
    "GRIEVANCE": [
        "grievance",
        "complaint",
        "complain",
        "appeal",
        "dispute",
        "redressal",
        "redress",
        "report a",
        "reporting a",
        "report the",
        "report this",
        "file a complaint",
        "file a grievance",
        "ફરિયાદ",
        "शिकायत",
        "रिपोर्ट",
        "अभियोग",
    ],
    "CONTACT": [
        "contact",
        "phone number",
        "email",
        "address",
        "helpline",
        "customer care",
        "સંપર્ક",
        "संपर्क",
    ],
    "SERVICE_ACCESS": [
        "access",
        "how to access",
        "use the service",
        "avail",
        "availment",
    ],
    "LOCATION": [
        "where",
        "location",
        "nearest",
        "near me",
        "address",
        "branch",
        "office",
    ],
    "COMPARISON": [
        "compare",
        "difference",
        "vs",
        "versus",
        "better",
        "which is better",
    ],
}


STATE_KEYWORDS = {
    # Gujarat
    "gujarat": "Gujarat",
    "ગુજરાત": "Gujarat",
    "गुजरात": "Gujarat",
    "ahmedabad": "Gujarat",
    "અમદાવાદ": "Gujarat",
    "surat": "Gujarat",
    "સુરત": "Gujarat",
    "vadodara": "Gujarat",
    "વડોદરા": "Gujarat",
    "rajkot": "Gujarat",
    "રાજકોટ": "Gujarat",
    "ikhedut": "Gujarat",
    "digitalgujarat": "Gujarat",

    # Maharashtra
    "maharashtra": "Maharashtra",
    "महाराष्ट्र": "Maharashtra",
    "mumbai": "Maharashtra",
    "मुंबई": "Maharashtra",
    "pune": "Maharashtra",
    "पुणे": "Maharashtra",
    "nagpur": "Maharashtra",
    "नागपुर": "Maharashtra",
    "mahaonline": "Maharashtra",

    # Madhya Pradesh
    "madhya pradesh": "Madhya Pradesh",
    "मध्य प्रदेश": "Madhya Pradesh",
    "bhopal": "Madhya Pradesh",
    "भोपाल": "Madhya Pradesh",
    "indore": "Madhya Pradesh",
    "इंदौर": "Madhya Pradesh",
    "mpedistrict": "Madhya Pradesh",

    # Rajasthan
    "rajasthan": "Rajasthan",
    "राजस्थान": "Rajasthan",
    "jaipur": "Rajasthan",
    "जयपुर": "Rajasthan",
    "jodhpur": "Rajasthan",
    "emitra": "Rajasthan",

    # Tamil Nadu
    "tamil nadu": "Tamil Nadu",
    "தமிழ்நாடு": "Tamil Nadu",
    "chennai": "Tamil Nadu",
    "சென்னை": "Tamil Nadu",
    "tnega": "Tamil Nadu",

    # West Bengal
    "west bengal": "West Bengal",
    "পশ্চিমবঙ্গ": "West Bengal",
    "kolkata": "West Bengal",
    "কলকাতা": "West Bengal",

    # Karnataka
    "karnataka": "Karnataka",
    "ಕರ್ನಾಟಕ": "Karnataka",
    "bangalore": "Karnataka",
    "bengaluru": "Karnataka",

    # Andhra Pradesh
    "andhra pradesh": "Andhra Pradesh",
    "ఆంధ్ర ప్రదేశ్": "Andhra Pradesh",

    # Uttar Pradesh
    "uttar pradesh": "Uttar Pradesh",
    "उत्तर प्रदेश": "Uttar Pradesh",
    "lucknow": "Uttar Pradesh",
    "लखनऊ": "Uttar Pradesh",

    # Bihar
    "bihar": "Bihar",
    "बिहार": "Bihar",
    "patna": "Bihar",
    "पटना": "Bihar",

    # Odisha
    "odisha": "Odisha",
    "ଓଡ଼ିଶା": "Odisha",

    # Punjab
    "punjab": "Punjab",
    "ਪੰਜਾਬ": "Punjab",
    "पंजाब": "Punjab",

    # Other States
    "arunachal pradesh": "Arunachal Pradesh",
    "assam": "Assam",
    "chhattisgarh": "Chhattisgarh",
    "goa": "Goa",
    "haryana": "Haryana",
    "himachal pradesh": "Himachal Pradesh",
    "jharkhand": "Jharkhand",
    "kerala": "Kerala",
    "manipur": "Manipur",
    "meghalaya": "Meghalaya",
    "mizoram": "Mizoram",
    "nagaland": "Nagaland",
    "sikkim": "Sikkim",
    "telangana": "Telangana",
    "tripura": "Tripura",
    "uttarakhand": "Uttarakhand",
}


class QueryClassifier:

    def classify(
        self, query: str, default_state: Optional[str] = None
    ) -> QueryClassification:

        text = query.lower().strip()

        if not text:
            raise ValueError("Query cannot be empty.")

        domain_scores = {}
        for domain, keywords in DOMAIN_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                if keyword in text:
                    # Multi-word or specific keywords add proportional weight
                    score += len(keyword.split())
            if score:
                domain_scores[domain] = score

        if domain_scores:
            domain = max(
                domain_scores,
                key=domain_scores.get,
            )
            highest_score = domain_scores[domain]
            confidence = min(
                1.0,
                0.55 + (highest_score * 0.1),
            )
        else:
            domain = "general"
            confidence = 0.25

        intent_scores = {}
        for intent, keywords in INTENT_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                if keyword in text:
                    score += 1
            if score:
                intent_scores[intent] = score

        if intent_scores:
            intent = max(
                intent_scores,
                key=intent_scores.get,
            )
        else:
            intent = "INFORMATIONAL"

        state = None
        for keyword, state_name in STATE_KEYWORDS.items():
            if keyword in text:
                state = state_name
                break

        if not state and default_state:
            state = default_state

        if state:
            jurisdiction = "state"
        else:
            jurisdiction = "central"

        return QueryClassification(
            domain=domain,
            jurisdiction=jurisdiction,
            state=state,
            intent=intent,
            confidence=round(confidence, 2),
        )
