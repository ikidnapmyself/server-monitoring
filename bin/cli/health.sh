# shellcheck shell=bash
# Sourced by cli.sh — do not execute directly.

health_menu() {
    while true; do
        show_banner
        if tuin_menu "Health" \
            "Run all health checks" \
            "Run specific checkers" \
            "Run a single checker" \
            "List available checkers" \
            "Preflight dashboard" \
            "JSON output (all checks)" \
            "CI mode: fail on warning" \
            "CI mode: fail on critical only"
        then
            case $TUIN_REPLY in
                "Run all health checks")
                    confirm_and_run "uv run python manage.py check_health" ;;
                "Run specific checkers")
                    run_command "uv run python manage.py check_health --list" "Listing checkers"
                    names=$(tuin_input "Enter checker names (space-separated)")
                    if [ -n "$names" ]; then
                        confirm_and_run "uv run python manage.py check_health $names"
                    else
                        echo -e "${RED}No checkers specified${NC}"
                    fi ;;
                "Run a single checker")
                    run_command "uv run python manage.py check_health --list" "Available checkers"
                    name=$(tuin_input "Enter checker name")
                    if [ -n "$name" ]; then
                        confirm_and_run "uv run python manage.py run_check $name"
                    else
                        echo -e "${RED}Checker name required${NC}"
                    fi ;;
                "List available checkers")
                    run_command "uv run python manage.py check_health --list" "Available checkers" ;;
                "Preflight dashboard")
                    confirm_and_run "uv run python manage.py preflight" ;;
                "JSON output (all checks)")
                    confirm_and_run "uv run python manage.py check_health --json" ;;
                "CI mode: fail on warning")
                    confirm_and_run "uv run python manage.py check_health --fail-on-warning" ;;
                "CI mode: fail on critical only")
                    confirm_and_run "uv run python manage.py check_health --fail-on-critical" ;;
            esac || true
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}