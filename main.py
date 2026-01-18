from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta
from typing import Optional, List
import json
import re
import os
from collections import defaultdict

app = FastAPI()

# ============================================================================
# DATABASE SIMULATION (will move to PostgreSQL)
# ============================================================================
companies = {}  # company_id -> company data
transactions = {}  # company_id -> list of transactions
groups = {}  # company_id -> list of transaction groups
forecasts = {}  # company_id -> forecast data
trend_settings = {}  # company_id -> trend adjustments

# Default categories
DEFAULT_CATEGORIES = [
    {"id": "payroll", "name": "Payroll", "icon": "💰", "frequency": "semi-monthly"},
    {"id": "payroll_tax", "name": "Payroll Taxes & Benefits", "icon": "🏛️", "frequency": "semi-monthly"},
    {"id": "rent", "name": "Rent", "icon": "🏢", "frequency": "monthly"},
    {"id": "utilities", "name": "Utilities (Phone, Internet, Electric)", "icon": "💡", "frequency": "monthly"},
    {"id": "insurance", "name": "Insurance", "icon": "🛡️", "frequency": "monthly"},
    {"id": "credit_card", "name": "Credit Card Payments", "icon": "💳", "frequency": "monthly"},
    {"id": "loan", "name": "Loan Payments", "icon": "🏦", "frequency": "monthly"},
    {"id": "cogs", "name": "Inventory / Cost of Goods", "icon": "📦", "frequency": "varies"},
    {"id": "sales_revenue", "name": "Sales Revenue", "icon": "💵", "frequency": "daily"},
    {"id": "other_revenue", "name": "Other Revenue", "icon": "📈", "frequency": "varies"},
    {"id": "distributions", "name": "Owner Distributions", "icon": "👤", "frequency": "uncommon"},
    {"id": "taxes", "name": "Taxes", "icon": "📋", "frequency": "quarterly"},
    {"id": "legal_accounting", "name": "Legal & Accounting", "icon": "⚖️", "frequency": "monthly"},
    {"id": "marketing", "name": "Marketing & Advertising", "icon": "📣", "frequency": "varies"},
    {"id": "subscriptions", "name": "Software & Subscriptions", "icon": "💻", "frequency": "monthly"},
    {"id": "daily_ops", "name": "Daily Operations", "icon": "🔧", "frequency": "daily"},
    {"id": "refunds", "name": "Refunds", "icon": "↩️", "frequency": "daily"},
    {"id": "unassigned", "name": "Unassigned", "icon": "❓", "frequency": "unknown"},
]

FREQUENCY_OPTIONS = [
    {"id": "daily", "name": "Daily (most business days)", "multiplier": 22},
    {"id": "weekly", "name": "Weekly", "multiplier": 4.33},
    {"id": "semi-monthly", "name": "Twice per Month", "multiplier": 2},
    {"id": "monthly", "name": "Monthly", "multiplier": 1},
    {"id": "quarterly", "name": "Quarterly", "multiplier": 0.33},
    {"id": "uncommon", "name": "Uncommon / One-time", "multiplier": 0},
    {"id": "varies", "name": "Varies", "multiplier": 1},
]

# ============================================================================
# ONBOARDING ENDPOINTS
# ============================================================================

@app.post("/api/company/create")
async def create_company(request: Request):
    """Create a new company profile"""
    data = await request.json()
    company_id = data.get("name", "").lower().replace(" ", "_")[:20] or f"co_{len(companies)+1}"
    
    companies[company_id] = {
        "id": company_id,
        "name": data.get("name", "My Company"),
        "website": data.get("website", ""),
        "logo_url": data.get("logo_url", ""),
        "primary_color": data.get("primary_color", "#FF8A65"),
        "secondary_color": data.get("secondary_color", "#FFA726"),
        "created_at": datetime.now().isoformat(),
        "setup_step": "data_upload"
    }
    transactions[company_id] = []
    groups[company_id] = []
    
    return {"success": True, "company_id": company_id, "access_code": company_id[:6]}

@app.get("/api/company/{company_id}")
async def get_company(company_id: str):
    """Get company profile"""
    if company_id not in companies:
        raise HTTPException(status_code=404, detail="Company not found")
    return companies[company_id]

@app.post("/api/company/{company_id}/fetch-branding")
async def fetch_branding(company_id: str, request: Request):
    """Fetch logo and colors from website"""
    data = await request.json()
    website = data.get("website", "")
    
    # In production, would scrape website for logo and extract colors
    # For now, return placeholder
    return {
        "logo_url": f"https://logo.clearbit.com/{website.replace('https://', '').replace('http://', '').split('/')[0]}" if website else "",
        "primary_color": "#FF8A65",
        "secondary_color": "#FFA726",
        "extracted": True
    }

# ============================================================================
# DATA IMPORT & PARSING
# ============================================================================

@app.post("/api/company/{company_id}/import-data")
async def import_data(company_id: str, request: Request):
    """Import bank transaction data"""
    if company_id not in companies:
        raise HTTPException(status_code=404, detail="Company not found")
    
    data = await request.json()
    raw_data = data.get("data", "")
    
    parsed = parse_bank_data(raw_data)
    
    # Store transactions
    transactions[company_id] = parsed["transactions"]
    
    # Auto-categorize
    auto_groups = auto_categorize_transactions(parsed["transactions"])
    groups[company_id] = auto_groups
    
    # Update setup step
    companies[company_id]["setup_step"] = "categorization"
    
    return {
        "success": True,
        "transactions_imported": len(parsed["transactions"]),
        "groups_detected": len(auto_groups),
        "date_range": {
            "start": parsed["start_date"],
            "end": parsed["end_date"]
        }
    }

def parse_bank_data(raw_data: str) -> dict:
    """Parse messy bank data into structured transactions"""
    transactions = []
    current_date = None
    
    lines = raw_data.strip().split('\n')
    
    # Date patterns
    date_patterns = [
        r'^([A-Z]{3})\s+(\d{1,2}),?\s*(\d{4})',  # JAN 13, 2026
        r'^(\d{1,2})/(\d{1,2})/(\d{4})',  # 01/13/2026
        r'^(\d{4})-(\d{2})-(\d{2})',  # 2026-01-13
    ]
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Check for date header
        date_match = None
        for pattern in date_patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                date_match = match
                break
        
        if date_match:
            # Parse date
            try:
                if 'JAN' in line.upper() or 'FEB' in line.upper():
                    # Month name format
                    month_name = date_match.group(1).upper()
                    day = int(date_match.group(2))
                    year = int(date_match.group(3))
                    months = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
                             'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
                    current_date = datetime(year, months.get(month_name, 1), day)
                elif '/' in line:
                    current_date = datetime.strptime(f"{date_match.group(1)}/{date_match.group(2)}/{date_match.group(3)}", "%m/%d/%Y")
                else:
                    current_date = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", "%Y-%m-%d")
            except:
                pass
            continue
        
        # Try to parse transaction line (tab or multiple-space separated)
        parts = re.split(r'\t+|\s{2,}', line)
        if len(parts) >= 3:
            try:
                description = parts[0] if len(parts[0]) > 5 else parts[1] if len(parts) > 1 else parts[0]
                
                # Find amounts (look for numbers with optional $ and commas)
                amounts = []
                for part in parts:
                    cleaned = re.sub(r'[$,]', '', part)
                    try:
                        amt = float(cleaned)
                        amounts.append(amt)
                    except:
                        pass
                
                if amounts:
                    # Determine if debit or credit
                    # Convention: if there are two amount columns, first is debit, second is credit
                    if len(amounts) >= 2:
                        debit = amounts[0] if amounts[0] > 0 else 0
                        credit = amounts[1] if len(amounts) > 1 and amounts[1] > 0 else 0
                        amount = credit - debit if credit else -debit
                    else:
                        amount = amounts[0]
                    
                    transactions.append({
                        "id": len(transactions) + 1,
                        "date": current_date.strftime("%Y-%m-%d") if current_date else datetime.now().strftime("%Y-%m-%d"),
                        "description": description.strip(),
                        "amount": amount,
                        "type": "credit" if amount > 0 else "debit",
                        "group_id": None,
                        "category_id": "unassigned"
                    })
            except Exception as e:
                pass
    
    dates = [t["date"] for t in transactions]
    return {
        "transactions": transactions,
        "start_date": min(dates) if dates else None,
        "end_date": max(dates) if dates else None
    }

def auto_categorize_transactions(txns: list) -> list:
    """Auto-detect transaction groups based on patterns"""
    groups = []
    grouped_txns = defaultdict(list)
    
    for txn in txns:
        desc = txn["description"].upper()
        amount = txn["amount"]
        
        # Pattern matching for common transaction types
        patterns = [
            (r'PAYROLL|PAYCHEX|ADP|GUSTO', 'payroll', 'Payroll'),
            (r'401K|RETIREMENT|PENSION', 'payroll_tax', 'Payroll Taxes & Benefits'),
            (r'RENT|LEASE', 'rent', 'Rent'),
            (r'ELECTRIC|GAS|WATER|UTILITY|PG&E|EDISON', 'utilities', 'Utilities'),
            (r'INSURANCE|GEICO|STATE FARM|BLUE CROSS|BLUE SHIELD', 'insurance', 'Insurance'),
            (r'AMEX|VISA|MASTERCARD|DISCOVER|CHASE CARD', 'credit_card', 'Credit Card'),
            (r'LOAN|MORTGAGE|LENDING', 'loan', 'Loan Payment'),
            (r'AMAZON|OFFICE DEPOT|STAPLES|SUPPLY', 'daily_ops', 'Office & Supplies'),
            (r'REFUND|RETURN', 'refunds', 'Refunds'),
            (r'DEPOSIT|MERCHANT|STRIPE|SQUARE|AUTHORIZE', 'sales_revenue', 'Sales Revenue'),
            (r'TRANSFER|WIRE|ACH', 'other_revenue', 'Bank Transfers'),
            (r'TAX|IRS|FTB|FRANCHISE', 'taxes', 'Taxes'),
            (r'ATTORNEY|LAWYER|CPA|ACCOUNTANT', 'legal_accounting', 'Legal & Accounting'),
            (r'GOOGLE ADS|FACEBOOK|META|MARKETING|ADVERTIS', 'marketing', 'Marketing'),
            (r'QUICKBOOKS|SLACK|ZOOM|ADOBE|MICROSOFT|SUBSCRIPTION', 'subscriptions', 'Software'),
        ]
        
        matched = False
        for pattern, cat_id, cat_name in patterns:
            if re.search(pattern, desc):
                key = f"{cat_id}_{cat_name}"
                grouped_txns[key].append(txn)
                txn["category_id"] = cat_id
                matched = True
                break
        
        if not matched:
            # Group by similar amounts for unmatched
            rounded_amt = round(abs(amount), -1)  # Round to nearest 10
            key = f"amount_{rounded_amt}_{txn['type']}"
            grouped_txns[key].append(txn)
    
    # Create group objects
    for key, txn_list in grouped_txns.items():
        if len(txn_list) >= 2:  # Only create groups with multiple transactions
            avg_amount = sum(t["amount"] for t in txn_list) / len(txn_list)
            frequency = detect_frequency(txn_list)
            
            # Get category from first transaction
            cat_id = txn_list[0].get("category_id", "unassigned")
            cat_name = next((c["name"] for c in DEFAULT_CATEGORIES if c["id"] == cat_id), "Unassigned")
            
            group = {
                "id": f"grp_{len(groups)+1}",
                "name": cat_name if cat_id != "unassigned" else f"Group {len(groups)+1}",
                "category_id": cat_id,
                "frequency": frequency,
                "avg_amount": avg_amount,
                "transaction_count": len(txn_list),
                "transaction_ids": [t["id"] for t in txn_list],
                "confirmed": False
            }
            groups.append(group)
            
            # Update transactions with group_id
            for t in txn_list:
                t["group_id"] = group["id"]
    
    return groups

def detect_frequency(txns: list) -> str:
    """Detect transaction frequency based on dates"""
    if len(txns) < 2:
        return "uncommon"
    
    dates = sorted([datetime.strptime(t["date"], "%Y-%m-%d") for t in txns])
    
    # Calculate average days between transactions
    gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
    avg_gap = sum(gaps) / len(gaps) if gaps else 30
    
    if avg_gap <= 2:
        return "daily"
    elif avg_gap <= 8:
        return "weekly"
    elif avg_gap <= 17:
        return "semi-monthly"
    elif avg_gap <= 35:
        return "monthly"
    elif avg_gap <= 100:
        return "quarterly"
    else:
        return "uncommon"

# ============================================================================
# CATEGORIZATION ENDPOINTS
# ============================================================================

@app.get("/api/company/{company_id}/groups")
async def get_groups(company_id: str):
    """Get all transaction groups for review"""
    if company_id not in companies:
        raise HTTPException(status_code=404, detail="Company not found")
    
    return {
        "groups": groups.get(company_id, []),
        "categories": DEFAULT_CATEGORIES,
        "frequencies": FREQUENCY_OPTIONS
    }

@app.get("/api/company/{company_id}/group/{group_id}")
async def get_group_detail(company_id: str, group_id: str):
    """Get group with its transactions"""
    if company_id not in groups:
        raise HTTPException(status_code=404, detail="Company not found")
    
    group = next((g for g in groups[company_id] if g["id"] == group_id), None)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    # Get transactions for this group
    group_txns = [t for t in transactions[company_id] if t["group_id"] == group_id]
    
    return {
        "group": group,
        "transactions": group_txns
    }

@app.post("/api/company/{company_id}/group/{group_id}/update")
async def update_group(company_id: str, group_id: str, request: Request):
    """Update group name, category, frequency"""
    data = await request.json()
    
    group = next((g for g in groups[company_id] if g["id"] == group_id), None)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    if "name" in data:
        group["name"] = data["name"]
    if "category_id" in data:
        group["category_id"] = data["category_id"]
    if "frequency" in data:
        group["frequency"] = data["frequency"]
    if "confirmed" in data:
        group["confirmed"] = data["confirmed"]
    
    return {"success": True, "group": group}

@app.post("/api/company/{company_id}/move-transactions")
async def move_transactions(company_id: str, request: Request):
    """Move selected transactions to a different group"""
    data = await request.json()
    txn_ids = data.get("transaction_ids", [])
    target_group_id = data.get("target_group_id")
    new_group_name = data.get("new_group_name")
    
    if new_group_name:
        # Create new group
        new_group = {
            "id": f"grp_{len(groups[company_id])+1}",
            "name": new_group_name,
            "category_id": data.get("category_id", "unassigned"),
            "frequency": data.get("frequency", "varies"),
            "avg_amount": 0,
            "transaction_count": 0,
            "transaction_ids": [],
            "confirmed": False
        }
        groups[company_id].append(new_group)
        target_group_id = new_group["id"]
    
    # Move transactions
    moved = 0
    for txn in transactions[company_id]:
        if txn["id"] in txn_ids:
            old_group_id = txn["group_id"]
            txn["group_id"] = target_group_id
            
            # Update old group
            if old_group_id:
                old_group = next((g for g in groups[company_id] if g["id"] == old_group_id), None)
                if old_group and txn["id"] in old_group["transaction_ids"]:
                    old_group["transaction_ids"].remove(txn["id"])
                    old_group["transaction_count"] -= 1
            
            # Update new group
            new_group = next((g for g in groups[company_id] if g["id"] == target_group_id), None)
            if new_group:
                new_group["transaction_ids"].append(txn["id"])
                new_group["transaction_count"] += 1
            
            moved += 1
    
    # Recalculate averages
    for grp in groups[company_id]:
        grp_txns = [t for t in transactions[company_id] if t["group_id"] == grp["id"]]
        if grp_txns:
            grp["avg_amount"] = sum(t["amount"] for t in grp_txns) / len(grp_txns)
    
    return {"success": True, "moved": moved}

# ============================================================================
# CASH FLOW MODEL
# ============================================================================

@app.get("/api/company/{company_id}/forecast")
async def get_forecast(company_id: str, days: int = 30):
    """Generate cash flow forecast"""
    if company_id not in companies:
        raise HTTPException(status_code=404, detail="Company not found")
    
    if company_id not in groups or not groups[company_id]:
        return {"error": "No transaction groups defined. Complete categorization first."}
    
    # Build forecast model from groups
    forecast_rows = []
    balance = companies[company_id].get("current_balance", 0)
    
    today = datetime.now().date()
    
    for day_offset in range(days):
        current_date = today + timedelta(days=day_offset)
        day_name = current_date.strftime("%A")
        is_weekend = day_name in ["Saturday", "Sunday"]
        
        daily_credits = 0
        daily_debits = 0
        transactions_today = []
        
        # Apply each group based on frequency
        for grp in groups[company_id]:
            if not grp.get("confirmed", False):
                continue
            
            freq = grp["frequency"]
            avg = grp["avg_amount"]
            
            should_apply = False
            
            if freq == "daily" and not is_weekend:
                should_apply = True
            elif freq == "weekly" and current_date.weekday() == 0:  # Mondays
                should_apply = True
            elif freq == "semi-monthly" and current_date.day in [1, 15]:
                should_apply = True
            elif freq == "monthly" and current_date.day == 1:
                should_apply = True
            elif freq == "quarterly" and current_date.day == 1 and current_date.month in [1, 4, 7, 10]:
                should_apply = True
            
            if should_apply:
                if avg > 0:
                    daily_credits += avg
                else:
                    daily_debits += abs(avg)
                transactions_today.append({
                    "name": grp["name"],
                    "amount": avg,
                    "type": "credit" if avg > 0 else "debit"
                })
        
        balance = balance + daily_credits - daily_debits
        
        forecast_rows.append({
            "date": current_date.strftime("%Y-%m-%d"),
            "day_name": day_name,
            "balance": round(balance, 2),
            "credits": round(daily_credits, 2),
            "debits": round(daily_debits, 2),
            "transactions": transactions_today
        })
    
    # Find high/low points
    balances = [r["balance"] for r in forecast_rows]
    low_idx = balances.index(min(balances))
    high_idx = balances.index(max(balances))
    
    return {
        "forecast": forecast_rows,
        "summary": {
            "current_balance": companies[company_id].get("current_balance", 0),
            "low_point": {
                "balance": forecast_rows[low_idx]["balance"],
                "date": forecast_rows[low_idx]["date"]
            },
            "high_point": {
                "balance": forecast_rows[high_idx]["balance"],
                "date": forecast_rows[high_idx]["date"]
            }
        }
    }

# ============================================================================
# TREND ANALYSIS
# ============================================================================

@app.get("/api/company/{company_id}/trends")
async def get_trends(company_id: str):
    """Analyze trends in transaction data"""
    if company_id not in transactions:
        raise HTTPException(status_code=404, detail="Company not found")
    
    txns = transactions[company_id]
    
    # Group by week and analyze
    weekly_totals = defaultdict(lambda: {"credits": 0, "debits": 0})
    
    for txn in txns:
        week = datetime.strptime(txn["date"], "%Y-%m-%d").isocalendar()[1]
        if txn["amount"] > 0:
            weekly_totals[week]["credits"] += txn["amount"]
        else:
            weekly_totals[week]["debits"] += abs(txn["amount"])
    
    weeks = sorted(weekly_totals.keys())
    
    # Calculate trends
    credit_trend = "stable"
    debit_trend = "stable"
    
    if len(weeks) >= 4:
        first_half_credits = sum(weekly_totals[w]["credits"] for w in weeks[:len(weeks)//2])
        second_half_credits = sum(weekly_totals[w]["credits"] for w in weeks[len(weeks)//2:])
        
        if second_half_credits > first_half_credits * 1.1:
            credit_trend = "increasing"
        elif second_half_credits < first_half_credits * 0.9:
            credit_trend = "decreasing"
        
        first_half_debits = sum(weekly_totals[w]["debits"] for w in weeks[:len(weeks)//2])
        second_half_debits = sum(weekly_totals[w]["debits"] for w in weeks[len(weeks)//2:])
        
        if second_half_debits > first_half_debits * 1.1:
            debit_trend = "increasing"
        elif second_half_debits < first_half_debits * 0.9:
            debit_trend = "decreasing"
    
    return {
        "weekly_data": [{"week": w, **weekly_totals[w]} for w in weeks],
        "trends": {
            "revenue": credit_trend,
            "expenses": debit_trend
        }
    }

@app.post("/api/company/{company_id}/trend-sentiment")
async def set_trend_sentiment(company_id: str, request: Request):
    """Set user's expectation for trends"""
    data = await request.json()
    
    trend_settings[company_id] = {
        "revenue_expectation": data.get("revenue", "continue"),  # continue, flatten, reverse
        "expense_expectation": data.get("expenses", "continue"),
        "notes": data.get("notes", "")
    }
    
    return {"success": True}

@app.post("/api/company/{company_id}/set-balance")
async def set_balance(company_id: str, request: Request):
    """Set current balance"""
    data = await request.json()
    companies[company_id]["current_balance"] = data.get("balance", 0)
    return {"success": True}

# ============================================================================
# STATIC FILES & FRONTEND
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    return open("/app/static/index.html").read()

# Mount static files
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
