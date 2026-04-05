# Sourced by cli.sh — do not execute directly.

system_menu() {
    show_banner
    echo -e "${BOLD}═══ System & Security ═══${NC}"
    echo ""

    local options=(
        "System check (full preflight)"
        "Security audit"
        "Set production mode"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "$SCRIPT_DIR/check_system.sh"
                ;;
            2)
                security_menu
                ;;
            3)
                confirm_and_run "$SCRIPT_DIR/set_production.sh"
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

security_menu() {
    show_banner
    echo -e "${BOLD}═══ Security Audit ═══${NC}"
    echo ""

    local options=(
        "Run security audit"
        "Run security audit (JSON output)"
        "Back"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "$SCRIPT_DIR/check_security.sh"
                ;;
            2)
                confirm_and_run "$SCRIPT_DIR/check_security.sh --json"
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