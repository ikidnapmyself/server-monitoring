# shellcheck shell=bash
# Sourced by cli.sh — do not execute directly.

pipeline_menu() {
    while true; do
        show_banner
        if tuin_menu "Pipeline" \
            "Run pipeline (sample payload)" \
            "Run pipeline by definition" \
            "Run pipeline from file" \
            "Run checks only (orchestrated)" \
            "Run checks only (dry run)" \
            "List pipeline definitions" \
            "Show one pipeline definition" \
            "List recent pipeline runs" \
            "Show one pipeline run"
        then
            case $TUIN_REPLY in
                "Run pipeline (sample payload)")
                    confirm_and_run "uv run python manage.py run_pipeline --sample" ;;
                "Run pipeline by definition")
                    run_command "uv run python manage.py show_pipeline --all" "Available pipeline definitions"
                    pipeline_name=$(tuin_input "Enter pipeline definition name")
                    if [ -n "$pipeline_name" ]; then
                        confirm_and_run "uv run python manage.py run_pipeline --definition $pipeline_name"
                    else
                        echo -e "${RED}Pipeline definition name required${NC}"
                    fi ;;
                "Run pipeline from file")
                    payload_path=$(tuin_input "Enter path to payload file")
                    if [ -n "$payload_path" ]; then
                        confirm_and_run "uv run python manage.py run_pipeline --file $payload_path"
                    else
                        echo -e "${RED}File path required${NC}"
                    fi ;;
                "Run checks only (orchestrated)")
                    confirm_and_run "uv run python manage.py run_pipeline --checks-only" ;;
                "Run checks only (dry run)")
                    confirm_and_run "uv run python manage.py run_pipeline --checks-only --dry-run" ;;
                "List pipeline definitions")
                    confirm_and_run "uv run python manage.py show_pipeline --all" ;;
                "Show one pipeline definition")
                    pipeline_name=$(tuin_input "Enter pipeline definition name")
                    if [ -n "$pipeline_name" ]; then
                        confirm_and_run "uv run python manage.py show_pipeline --name $pipeline_name"
                    else
                        echo -e "${RED}Pipeline definition name required${NC}"
                    fi ;;
                "List recent pipeline runs")
                    confirm_and_run "uv run python manage.py monitor_pipeline" ;;
                "Show one pipeline run")
                    run_id=$(tuin_input "Enter pipeline run id")
                    if [ -n "$run_id" ]; then
                        confirm_and_run "uv run python manage.py monitor_pipeline --run-id $run_id"
                    else
                        echo -e "${RED}Run id required${NC}"
                    fi ;;
            esac || true
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}