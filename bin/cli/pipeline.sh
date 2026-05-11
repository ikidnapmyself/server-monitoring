# Sourced by cli.sh — do not execute directly.

pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ Pipeline ═══${NC}"
    echo ""

    local options=(
        "Run pipeline (sample payload)"
        "Run pipeline by definition"
        "Run pipeline from file"
        "Run checks only (orchestrated)"
        "Run checks only (dry run)"
        "List pipeline definitions"
        "Show one pipeline definition"
        "List recent pipeline runs"
        "Show one pipeline run"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py run_pipeline --sample"
                ;;
            2)
                echo ""
                run_command "uv run python manage.py show_pipeline --all" "Available pipeline definitions"
                echo ""
                read -p "Enter pipeline definition name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline --definition $pipeline_name"
                else
                    echo -e "${RED}Pipeline definition name required${NC}"
                fi
                ;;
            3)
                read -p "Enter path to payload file: " payload_path
                if [ -n "$payload_path" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline --file $payload_path"
                else
                    echo -e "${RED}File path required${NC}"
                fi
                ;;
            4)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only"
                ;;
            5)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only --dry-run"
                ;;
            6)
                confirm_and_run "uv run python manage.py show_pipeline --all"
                ;;
            7)
                read -p "Enter pipeline definition name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py show_pipeline --name $pipeline_name"
                else
                    echo -e "${RED}Pipeline definition name required${NC}"
                fi
                ;;
            8)
                confirm_and_run "uv run python manage.py monitor_pipeline"
                ;;
            9)
                read -p "Enter pipeline run id: " run_id
                if [ -n "$run_id" ]; then
                    confirm_and_run "uv run python manage.py monitor_pipeline --run-id $run_id"
                else
                    echo -e "${RED}Run id required${NC}"
                fi
                ;;
            10)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}