# Sourced by cli.sh — do not execute directly.

system_menu() {
    show_banner
    echo -e "${BOLD}═══ System & Security ═══${NC}"
    echo ""

    local options=(
        "Run preflight checks"
        "Set production mode"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py preflight"
                ;;
            2)
                confirm_and_run "$SCRIPT_DIR/set_production.sh"
                ;;
            3)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}