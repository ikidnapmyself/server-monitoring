# shellcheck shell=bash
# Sourced by cli.sh — do not execute directly.

pipeline_menu() {
    while true; do
        show_banner
        if tuin_menu "Pipeline" \
            "Run pipeline (sample payload)" \
            "Run pipeline from file" \
            "Run this machine's checks through the pipeline" \
            "List recent pipeline runs" \
            "Show one pipeline run" \
            "Trace an alert's journey" \
            "Report (nodes / pipelines / inbox)"
        then
            case $TUIN_REPLY in
                "Run pipeline (sample payload)")
                    confirm_and_run "$UV_BIN run python manage.py run_pipeline --sample" ;;
                "Run pipeline from file")
                    payload_path=$(tuin_input "Enter path to payload file")
                    if [ -n "$payload_path" ]; then
                        confirm_and_run "$UV_BIN run python manage.py run_pipeline --file $payload_path"
                    else
                        echo -e "${RED}File path required${NC}"
                    fi ;;
                "Run this machine's checks through the pipeline")
                    # check_health is the local entrypoint: it runs this machine's
                    # checkers, records their alerts, and drains the run each
                    # materially changed incident earns — synchronously, no daemon.
                    # Replaces the deprecated run_pipeline --checks-only. There is
                    # no dry-run counterpart: use --no-alert to run the checkers and
                    # write nothing, so no incident forms and nothing is enqueued.
                    confirm_and_run "$UV_BIN run python manage.py check_health" ;;
                "List recent pipeline runs")
                    confirm_and_run "$UV_BIN run python manage.py monitor_pipeline" ;;
                "Show one pipeline run")
                    run_id=$(tuin_input "Enter pipeline run id")
                    if [ -n "$run_id" ]; then
                        confirm_and_run "$UV_BIN run python manage.py monitor_pipeline --run-id $run_id"
                    else
                        echo -e "${RED}Run id required${NC}"
                    fi ;;
                "Trace an alert's journey")
                    target=$(tuin_input "Enter alert id or trace_id")
                    if [ -n "$target" ]; then
                        confirm_and_run "$UV_BIN run python manage.py trace $target"
                    else
                        echo -e "${RED}Alert id or trace_id required${NC}"
                    fi ;;
                "Report (nodes / pipelines / inbox)")
                    confirm_and_run "$UV_BIN run python manage.py report" ;;
            esac || true
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}