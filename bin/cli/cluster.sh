# Sourced by cli.sh — do not execute directly.

cluster_menu() {
    show_banner
    echo -e "${BOLD}═══ Cluster ═══${NC}"
    echo ""
    echo "Push local check results to a hub instance (cluster mode)"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  --dry-run          Show what would be pushed without sending"
    echo "  --checkers a,b,c   Run only specific checkers (comma-separated)"
    echo ""

    local options=(
        "Push checks to hub"
        "Push checks to hub (dry run)"
        "Push checks to hub (specific checkers)"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py push_to_hub"
                ;;
            2)
                confirm_and_run "uv run python manage.py push_to_hub --dry-run"
                ;;
            3)
                read -p "Enter checker names (comma-separated): " checker_names
                if [ -n "$checker_names" ]; then
                    confirm_and_run "uv run python manage.py push_to_hub --checkers $checker_names"
                else
                    echo -e "${RED}Checker names required${NC}"
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