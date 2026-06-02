# shellcheck shell=bash
# Sourced by cli.sh — do not execute directly.

cluster_menu() {
    while true; do
        show_banner
        tuin_section "Cluster"
        echo "Push local check results to a hub instance (cluster mode)."
        echo ""
        if tuin_menu "Cluster" \
            "Push checks to hub" \
            "Push checks to hub (dry run)" \
            "Push checks to hub (specific checkers)"
        then
            case $TUIN_REPLY in
                "Push checks to hub")
                    confirm_and_run "uv run python manage.py push_to_hub" ;;
                "Push checks to hub (dry run)")
                    confirm_and_run "uv run python manage.py push_to_hub --dry-run" ;;
                "Push checks to hub (specific checkers)")
                    checker_names=$(tuin_input "Enter checker names (comma-separated)")
                    if [ -n "$checker_names" ]; then
                        confirm_and_run "uv run python manage.py push_to_hub --checkers $checker_names"
                    else
                        echo -e "${RED}Checker names required${NC}"
                    fi ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}