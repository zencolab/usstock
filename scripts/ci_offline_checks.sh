#!/usr/bin/env bash
# Offline verification for every pipeline in this repository.
#
# The same command runs locally and in CI:
#
#     bash scripts/ci_offline_checks.sh
#
# Offline means no market-data API call, no Ollama call, no Google Drive upload
# and no GitHub Pages publish.
#
# Every phase runs even when an earlier one fails, so a single run reports the
# complete picture:
#   * ci-status.txt  one line per phase
#   * ci-log.txt     full output of every phase
#
# Dependencies are installed only in CI. Set SKIP_INSTALL=1 to skip that even
# there, or PYTHON=... to pick a different interpreter.

set -u

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
LOG_FILE="${LOG_FILE:-ci-log.txt}"
STATUS_FILE="${STATUS_FILE:-ci-status.txt}"

: > "$LOG_FILE"
: > "$STATUS_FILE"

required_failed=0

run_phase() {
	local name="$1"
	local required="$2"
	shift 2
	{
		echo
		echo "===== ${name} ====="
	} >> "$LOG_FILE"
	local code=0
	"$@" >> "$LOG_FILE" 2>&1 || code=$?
	if [ "$code" -eq 0 ]; then
		echo "${name}: ok" >> "$STATUS_FILE"
	elif [ "$required" = "required" ]; then
		echo "${name}: FAILED (exit ${code})" >> "$STATUS_FILE"
		required_failed=1
	else
		echo "${name}: failed, not blocking (exit ${code})" >> "$STATUS_FILE"
	fi
	tail -n 1 "$STATUS_FILE"
	return 0
}

skip_phase() {
	echo "$1: skipped ($2)" >> "$STATUS_FILE"
	tail -n 1 "$STATUS_FILE"
}

if [ -n "${CI:-}" ] && [ "${SKIP_INSTALL:-0}" = "0" ]; then
	run_phase "upgrade pip" optional "$PYTHON" -m pip install --upgrade pip
	run_phase "install root requirements" required \
		"$PYTHON" -m pip install -r requirements.txt
	if [ -f hourly_news_bot/requirements.txt ]; then
		run_phase "install news bot requirements" required \
			"$PYTHON" -m pip install -r hourly_news_bot/requirements.txt
	fi
else
	skip_phase "install dependencies" "not running in CI"
fi

run_phase "byte-compile the review fix modules" required \
	"$PYTHON" -m py_compile \
	template_env.py \
	runtime_config.py \
	report_asof.py \
	russell2000_market_report/universe_snapshot.py \
	russell2000_market_report/universe.py \
	russell2000_market_report/runtime.py \
	hourly_news_bot/src/state.py \
	hourly_news_bot/src/main.py \
	scripts/run_offline_tests.py \
	scripts/assert_escaped_output.py \
	scripts/compile_assembled_source.py

run_phase "byte-compile the existing pipeline modules" required \
	"$PYTHON" -m py_compile \
	market_report.py \
	hybrid_data.py \
	hybrid_runtime.py \
	bilingual_runtime.py \
	news_translation.py \
	premium_translation.py \
	ai_translation.py \
	scripts/validate_output.py \
	scripts/render_market_report_pdf.py

run_phase "compile the assembled market_report source" required \
	"$PYTHON" scripts/compile_assembled_source.py

run_phase "code-review regression tests" required \
	"$PYTHON" scripts/run_offline_tests.py --regressions

run_phase "every offline test suite" required \
	"$PYTHON" scripts/run_offline_tests.py

run_phase "offline demo report" optional \
	"$PYTHON" market_report.py --mode demo --top-n 2 \
	--output site-smoke --data-output output-smoke

if [ -d site-smoke ]; then
	run_phase "scan the demo output for unsafe link schemes" required \
		"$PYTHON" scripts/assert_escaped_output.py site-smoke
else
	skip_phase "scan the demo output for unsafe link schemes" "no demo output"
fi

echo
echo "===== phase results ====="
cat "$STATUS_FILE"
echo
echo "===== full log ====="
cat "$LOG_FILE"

exit "$required_failed"
