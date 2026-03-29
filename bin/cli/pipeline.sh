# Sourced by cli.sh — do not execute directly.

pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ Pipeline Orchestration ═══${NC}"
    echo ""

    local options=(
        "show_pipeline - View pipeline definitions"
        "run_pipeline - Execute a pipeline"
        "monitor_pipeline - Monitor pipeline execution"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1) show_pipeline_menu ;;
            2) run_pipeline_menu ;;
            3) monitor_pipeline_menu ;;
            4) return ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}

show_pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ show_pipeline ═══${NC}"
    echo ""
    echo "View pipeline definitions and their configuration"
    echo ""

    local options=(
        "Show all active pipelines"
        "Show all pipelines (including inactive)"
        "Show specific pipeline"
        "Show as JSON"
        "Back"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py show_pipeline"
                ;;
            2)
                confirm_and_run "uv run python manage.py show_pipeline --all"
                ;;
            3)
                read -p "Enter pipeline name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py show_pipeline --name $pipeline_name"
                else
                    echo -e "${RED}Pipeline name required${NC}"
                fi
                ;;
            4)
                confirm_and_run "uv run python manage.py show_pipeline --json"
                ;;
            5)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

run_pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ run_pipeline ═══${NC}"
    echo ""
    echo "Execute a pipeline definition"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  PIPELINE_NAME      Name of pipeline (or path to definition)"
    echo "  --list             List available pipelines"
    echo "  --dry-run          Show what would be executed"
    echo "  --json             Output as JSON"
    echo ""

    local options=(
        "List available pipelines"
        "Run a pipeline"
        "Dry run (preview)"
        "Back"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py run_pipeline --list"
                ;;
            2)
                read -p "Enter pipeline name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline $pipeline_name"
                else
                    echo -e "${RED}Pipeline name required${NC}"
                fi
                ;;
            3)
                read -p "Enter pipeline name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline $pipeline_name --dry-run"
                else
                    echo -e "${RED}Pipeline name required${NC}"
                fi
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

monitor_pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ monitor_pipeline ═══${NC}"
    echo ""
    echo "Monitor pipeline execution status"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  PIPELINE_ID        ID of pipeline execution to monitor"
    echo "  --list             List recent pipeline executions"
    echo "  --follow           Follow execution in real-time"
    echo ""

    local options=(
        "List recent executions"
        "Monitor a pipeline"
        "Follow pipeline (real-time)"
        "Back"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py monitor_pipeline --list"
                ;;
            2)
                read -p "Enter pipeline execution ID: " pipeline_id
                if [ -n "$pipeline_id" ]; then
                    confirm_and_run "uv run python manage.py monitor_pipeline $pipeline_id"
                else
                    echo -e "${RED}Pipeline ID required${NC}"
                fi
                ;;
            3)
                read -p "Enter pipeline execution ID: " pipeline_id
                if [ -n "$pipeline_id" ]; then
                    confirm_and_run "uv run python manage.py monitor_pipeline $pipeline_id --follow"
                else
                    echo -e "${RED}Pipeline ID required${NC}"
                fi
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}