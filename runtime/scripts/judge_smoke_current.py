"""One current smoke: actual browser, MCP and all completion regressions."""
import subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
raise SystemExit(subprocess.call([sys.executable,'-m','pytest',str(root/'tests'),'-q','-p','no:cacheprovider','--basetemp',str(root/'evidence'/'smoke-temp'),'--junitxml',str(root/'evidence'/'SMOKE_JUNIT.xml')],cwd=root))
