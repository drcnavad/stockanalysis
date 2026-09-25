"""Run the regression tests:  python tests/run_tests.py  [--fast = skip the app / dashboard tests]
  test_midweek_repro.py  live engine reproduces the tested backtests exactly (+438.08% / 1.4649 plain MW; the live exit rule)
  test_rank_audit.py     saved ranks, scores, picks and mid-week decisions re-derived independently from raw bars
  test_runner.py         run_all.py mode choice + NewsAPI once-a-day guard (pure logic, no API calls)
  test_app.py            Streamlit AppTest: page, charts, displayed ranks, Details widgets, captions, holdings alert
  test_paper_account.py  alpaca_paper.py + alpaca_paper_account.ipynb + run_all --sync-paper against a local MOCK server
  test_dashboard_http.py the app on test port 8599 answers 200 for / and /?symbol=NVDA (never touches 8501)
Read-only for the project (temporary files only, deleted afterwards). No quota APIs, no Alpaca account calls."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTS = ["test_midweek_repro.py", "test_rank_audit.py", "test_runner.py", "test_app.py", "test_paper_account.py",
         "test_dashboard_http.py"]


def main():
    fast = "--fast" in sys.argv
    env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONDONTWRITEBYTECODE="1",
               PYTHONWARNINGS="ignore")
    results = []
    for t in TESTS:
        if fast and t in ("test_app.py", "test_dashboard_http.py"):
            continue
        if not os.path.exists(os.path.join(HERE, t)):
            continue
        t0 = time.time()
        p = subprocess.run([sys.executable, os.path.join(HERE, t)], cwd=ROOT, env=env, capture_output=True, text=True)
        ok = p.returncode == 0
        results.append((t, ok, time.time() - t0))
        tail = [l for l in (p.stdout + p.stderr).splitlines() if l.strip()][-4 if ok else -25:]
        print(f"{'PASS' if ok else 'FAIL'}  {t}  ({time.time() - t0:.0f}s)")
        for line in tail:
            print("      " + line)
    n_ok = sum(ok for _, ok, _ in results)
    print(f"\n{n_ok}/{len(results)} test files passed")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
