import json
import sys
from pathlib import Path
from scorer import rank_jobs

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

def main():
    if len(sys.argv) < 2:
        print("Usage: python agent/main.py jobs.json"); raise SystemExit(2)
    jobs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    ranked = rank_jobs(jobs, CFG)
    for i, item in enumerate(ranked[:CFG["max_proposals_to_consider"]], 1):
        print("#" + str(i) + " [" + str(item["score"]) + "/100] " + item["title"] + " (" + item["job_id"] + ")")
        for reason in item["reasons"]: print("  + " + reason)
        for risk in item["risks"]: print("  ! " + risk)
        print()

if __name__ == "__main__": main()
