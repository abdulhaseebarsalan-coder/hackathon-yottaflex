"""Hand-authored reference data: organization, role templates, access catalog and name pools.

Everything here is invented for this project (no external or personal data). Names are generic
and combined at random, and emails use the reserved domain example.com (RFC 2606), so no
generated identity corresponds to a real person.
"""

from __future__ import annotations

# --------------------------------------------------------------------------------------
# Departments: (name, function_group, cost_center, share of new hires)
# --------------------------------------------------------------------------------------
DEPARTMENTS = [
    ("Executive Office", "BUSINESS", "CC-1000", 0.00),
    ("Engineering", "BUSINESS", "CC-1100", 0.28),
    ("Product Management", "BUSINESS", "CC-1200", 0.06),
    ("Data & Analytics", "BUSINESS", "CC-1300", 0.07),
    ("Sales", "BUSINESS", "CC-1400", 0.16),
    ("Customer Success", "BUSINESS", "CC-1500", 0.12),
    ("Marketing", "BUSINESS", "CC-1600", 0.07),
    ("Finance", "BUSINESS", "CC-1700", 0.06),
    ("Legal & Compliance", "BUSINESS", "CC-1800", 0.03),
    ("People (HR)", "HR", "CC-2000", 0.05),
    ("IT & Security", "IT", "CC-2100", 0.06),
    ("Facilities & Workplace", "FACILITIES", "CC-2200", 0.04),
]

# --------------------------------------------------------------------------------------
# Roles per department: (title, job_level, kind, laptop_class, contractor_eligible)
#   kind: CEO | HEAD | LEAD (people managers) | HRBP | ITOPS | SYSADMIN | WORKPLACE | IC | INTERN
#   The workflow-partner kinds (HRBP / ITOPS / SYSADMIN / WORKPLACE) staff the cases.
# --------------------------------------------------------------------------------------
MANAGER_KINDS = {"CEO", "HEAD", "LEAD"}

ROLES = {
    "Executive Office": [
        ("Chief Executive Officer", "L7", "CEO", "STANDARD", False),
        ("Executive Assistant", "L2", "IC", "STANDARD", False),
    ],
    "Engineering": [
        ("Director of Engineering", "L6", "HEAD", "ENGINEERING", False),
        ("Engineering Manager", "L5", "LEAD", "ENGINEERING", False),
        ("Software Engineer", "L3", "IC", "ENGINEERING", True),
        ("Senior Software Engineer", "L4", "IC", "ENGINEERING", True),
        ("Site Reliability Engineer", "L4", "IC", "ENGINEERING", True),
        ("QA Engineer", "L3", "IC", "ENGINEERING", True),
        ("Software Engineering Intern", "L1", "INTERN", "ENGINEERING", False),
    ],
    "Product Management": [
        ("Head of Product", "L6", "HEAD", "STANDARD", False),
        ("Group Product Manager", "L5", "LEAD", "STANDARD", False),
        ("Product Manager", "L4", "IC", "STANDARD", True),
        ("Product Designer", "L3", "IC", "ENGINEERING", True),
    ],
    "Data & Analytics": [
        ("Head of Data", "L6", "HEAD", "ENGINEERING", False),
        ("Analytics Manager", "L5", "LEAD", "ENGINEERING", False),
        ("Data Analyst", "L3", "IC", "STANDARD", True),
        ("Data Engineer", "L4", "IC", "ENGINEERING", True),
        ("Data Science Intern", "L1", "INTERN", "STANDARD", False),
    ],
    "Sales": [
        ("VP Sales", "L6", "HEAD", "STANDARD", False),
        ("Sales Manager", "L5", "LEAD", "STANDARD", False),
        ("Account Executive", "L3", "IC", "STANDARD", False),
        ("Sales Development Rep", "L2", "IC", "STANDARD", True),
    ],
    "Customer Success": [
        ("Head of Customer Success", "L6", "HEAD", "STANDARD", False),
        ("Support Team Lead", "L4", "LEAD", "STANDARD", False),
        ("Customer Success Manager", "L3", "IC", "STANDARD", True),
        ("Support Specialist", "L2", "IC", "STANDARD", True),
    ],
    "Marketing": [
        ("Head of Marketing", "L6", "HEAD", "STANDARD", False),
        ("Marketing Manager", "L5", "LEAD", "STANDARD", False),
        ("Content Marketer", "L3", "IC", "STANDARD", True),
        ("Growth Marketer", "L3", "IC", "STANDARD", True),
        ("Marketing Intern", "L1", "INTERN", "STANDARD", False),
    ],
    "Finance": [
        ("Chief Financial Officer", "L6", "HEAD", "STANDARD", False),
        ("Finance Manager", "L5", "LEAD", "STANDARD", False),
        ("Financial Analyst", "L3", "IC", "STANDARD", True),
        ("Accountant", "L3", "IC", "STANDARD", True),
    ],
    "Legal & Compliance": [
        ("General Counsel", "L6", "HEAD", "STANDARD", False),
        ("Legal Counsel", "L4", "IC", "STANDARD", True),
        ("Compliance Analyst", "L3", "IC", "STANDARD", True),
    ],
    "People (HR)": [
        ("HR Director", "L6", "HEAD", "STANDARD", False),
        ("HR Business Partner", "L4", "HRBP", "STANDARD", False),
        ("Recruiter", "L3", "IC", "STANDARD", True),
        ("People Operations Specialist", "L2", "IC", "STANDARD", True),
    ],
    "IT & Security": [
        ("IT Director", "L6", "HEAD", "ENGINEERING", False),
        ("IT Operations Partner", "L3", "ITOPS", "STANDARD", False),
        ("Systems Administrator", "L4", "SYSADMIN", "ENGINEERING", False),
        ("Security Engineer", "L4", "IC", "ENGINEERING", True),
        ("Service Desk Analyst", "L2", "IC", "STANDARD", True),
    ],
    "Facilities & Workplace": [
        ("Facilities Manager", "L5", "HEAD", "STANDARD", False),
        ("Workplace Coordinator", "L2", "WORKPLACE", "NONE", False),
        ("Facilities Technician", "L2", "IC", "NONE", True),
    ],
}

# --------------------------------------------------------------------------------------
# Access catalog: key -> (name, target_system, privilege_level, approver_type, contractor_allowed)
# Generic system names on purpose (no vendor branding).
# Corporate SSO & Email is a mandatory, approval-gated AccessGrant for every role: approved in
# ONB_ACCESS_APPROVAL, provisioned by ONB_IDENTITY_SSO, revoked by OFF_REVOKE_ACCESS.
# --------------------------------------------------------------------------------------
ACCESS_ROLES = {
    "SSO": ("Corporate SSO & Email", "Identity Provider", "STANDARD", "MANAGER", True),
    "VPN": ("Remote Access VPN", "Network", "STANDARD", "MANAGER", True),
    "CODE": ("Source Code Repository - Write", "Code Hosting", "ELEVATED", "MANAGER", True),
    "CLOUD": ("Cloud Console - Developer", "Cloud Platform", "ELEVATED", "MANAGER_AND_SYSTEM_OWNER", True),
    "PROD_DB": ("Production Database - Admin", "Production Database", "PRIVILEGED", "MANAGER_AND_SYSTEM_OWNER", False),
    "CRM": ("CRM - Sales User", "CRM", "STANDARD", "MANAGER", True),
    "SUPPORT": ("Support Desk - Agent", "Ticketing", "STANDARD", "MANAGER", True),
    "ERP": ("ERP - Finance Ledger", "ERP", "ELEVATED", "MANAGER_AND_SYSTEM_OWNER", False),
    "HRIS_ADMIN": ("HRIS & Payroll - Admin", "HRIS", "PRIVILEGED", "MANAGER_AND_SYSTEM_OWNER", False),
    "MKT": ("Marketing Automation - Editor", "Marketing Platform", "STANDARD", "MANAGER", True),
    "BI": ("BI Dashboards - Viewer", "BI Platform", "STANDARD", "MANAGER", True),
    "DWH": ("Data Warehouse - Analyst", "Data Warehouse", "ELEVATED", "MANAGER_AND_SYSTEM_OWNER", True),
    "IDP_ADMIN": ("Identity Provider - Admin", "Identity Provider", "PRIVILEGED", "MANAGER_AND_SYSTEM_OWNER", False),
    "BUILDING": ("Building Access Control - Operator", "Physical Security", "ELEVATED", "MANAGER_AND_SYSTEM_OWNER", False),
    "CLM": ("Contract Management - User", "Contract Lifecycle", "STANDARD", "MANAGER", True),
}

# Role templates. Every role gets SSO (mandatory); department defaults and role overrides add on top.
DEPARTMENT_ACCESS = {
    "Executive Office": [("BI", False)],
    "Engineering": [("CODE", True), ("CLOUD", False), ("VPN", False)],
    "Product Management": [("BI", False), ("CODE", False)],
    "Data & Analytics": [("DWH", True), ("BI", False)],
    "Sales": [("CRM", True)],
    "Customer Success": [("SUPPORT", True), ("CRM", False)],
    "Marketing": [("MKT", True), ("CRM", False)],
    "Finance": [("ERP", True), ("BI", False)],
    "Legal & Compliance": [("CLM", True)],
    "People (HR)": [],
    "IT & Security": [("VPN", False)],
    "Facilities & Workplace": [("BUILDING", False)],
}
ROLE_ACCESS_OVERRIDES = {
    "Site Reliability Engineer": [("PROD_DB", False)],
    "Senior Software Engineer": [("PROD_DB", False)],
    "HR Business Partner": [("HRIS_ADMIN", True)],
    "HR Director": [("HRIS_ADMIN", True)],
    "Systems Administrator": [("IDP_ADMIN", True)],
    "Security Engineer": [("IDP_ADMIN", False)],
    "Workplace Coordinator": [("BUILDING", True)],
    "Chief Financial Officer": [("ERP", True)],
}

# --------------------------------------------------------------------------------------
# Asset catalog: type -> list of (model, unit_cost_usd)
# --------------------------------------------------------------------------------------
ASSET_MODELS = {
    "LAPTOP": {
        "STANDARD": [("Standard Laptop 14in G5", 1150.00), ("Standard Laptop 14in G6", 1240.00)],
        "ENGINEERING": [("Engineering Laptop 16in Pro", 2650.00), ("Engineering Laptop 16in Max", 3180.00)],
    },
    "MONITOR": [("27in 4K Monitor", 389.00), ("34in Ultrawide Monitor", 549.00)],
    "PHONE": [("Corporate Smartphone 128GB", 699.00)],
    "SECURITY_KEY": [("FIDO2 Hardware Security Key", 55.00)],
}

# --------------------------------------------------------------------------------------
# Synthetic name pools (common, multicultural given/family names; combined at random)
# --------------------------------------------------------------------------------------
FIRST_NAMES = [
    "Aarav", "Aisha", "Alex", "Amara", "Ana", "Ben", "Carlos", "Chen", "Chloe", "Daniel",
    "Diego", "Elena", "Emma", "Fatima", "Felix", "Grace", "Hana", "Hassan", "Ines", "Isaac",
    "Ivan", "Jade", "James", "Jin", "Julia", "Kai", "Kemal", "Lara", "Leo", "Lina",
    "Lucas", "Maya", "Mei", "Mohammed", "Nadia", "Noah", "Nora", "Omar", "Priya", "Rafael",
    "Ravi", "Rosa", "Sam", "Sara", "Sofia", "Tariq", "Theo", "Uma", "Victor", "Wei",
    "Yara", "Yusuf", "Zara", "Zoe", "Arjun", "Bilal", "Clara", "Dmitri", "Esi", "Kofi",
]
LAST_NAMES = [
    "Adams", "Ahmed", "Ali", "Alvarez", "Baker", "Bauer", "Brown", "Chen", "Costa", "Das",
    "Diaz", "Evans", "Fischer", "Garcia", "Gupta", "Haddad", "Hansen", "Hughes", "Ibrahim", "Ito",
    "Jensen", "Johnson", "Kim", "Khan", "Kowalski", "Kumar", "Lee", "Lopez", "Martin", "Mensah",
    "Meyer", "Moreau", "Murphy", "Nakamura", "Nguyen", "Novak", "Okafor", "Olsen", "Patel", "Perez",
    "Petrov", "Rahman", "Reyes", "Rossi", "Santos", "Schmidt", "Shah", "Silva", "Singh", "Smith",
    "Tanaka", "Taylor", "Torres", "Wagner", "Walker", "Wang", "Weber", "Wilson", "Yilmaz", "Zhang",
]
