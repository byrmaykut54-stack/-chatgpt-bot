import re
from dataclasses import dataclass, asdict

@dataclass
class ScoredJob:
    job_id: str
    title: str
    score: int
    reasons: list[str]
    risks: list[str]

def _money_range(job):
    budget = job.get("budget")
    if not budget: return 0.0, 0.0
    nums = [float(x.replace(",", "")) for x in re.findall(r"\\d+(?:\\.\\d+)?", str(budget))]
    return (min(nums), max(nums)) if nums else (0.0, 0.0)

def score_job(job, cfg):
    text = (str(job.get("title","")) + " " + str(job.get("description_snippet","")) + " " + " ".join(job.get("skills", []))).lower()
    score, reasons, risks = 0, [], []
    matched = [k for k in cfg["keywords"] if k.lower() in text]
    if matched:
        score += min(40, len(matched) * 8); reasons.append("Relevant: " + ", ".join(matched[:5]))
    _, high = _money_range(job)
    if job.get("job_type") == "hourly":
        if high >= cfg["min_hourly_rate_usd"]: score += 20; reasons.append("Hourly budget reaches $" + str(high) + "/hr")
        else: risks.append("Budget below preferred hourly threshold")
    elif job.get("job_type") == "fixed":
        if high >= cfg["min_fixed_budget_usd"]: score += 20; reasons.append("Fixed budget reaches $" + str(high))
        else: risks.append("Fixed budget below preferred threshold")
    client = job.get("client", {})
    if cfg["prefer_verified_payment"] and client.get("verification_status") == "VERIFIED": score += 10; reasons.append("Payment method verified")
    proposals = str(job.get("proposals_tier", ""))
    if cfg["prefer_low_proposal_count"]:
        if "Fewer than 5" in proposals: score += 15; reasons.append("Low proposal competition")
        elif "5 to 10" in proposals: score += 10
        elif "10 to 15" in proposals: score += 5
        elif "50+" in proposals: risks.append("High proposal competition")
    hires = client.get("total_hires")
    if isinstance(hires, (int, float)) and hires > 0: score += min(10, int(hires / 10) + 2); reasons.append("Client has " + str(int(hires)) + " prior hires")
    for bad in cfg.get("exclude_keywords", []):
        if bad.lower() in text: score -= 50; risks.append("Excluded keyword: " + bad)
    return ScoredJob(str(job.get("id")), job.get("title", ""), max(0,min(100,score)), reasons, risks)

def rank_jobs(jobs, cfg):
    ranked = [score_job(j, cfg) for j in jobs]
    ranked.sort(key=lambda x: x.score, reverse=True)
    return [asdict(x) for x in ranked]
