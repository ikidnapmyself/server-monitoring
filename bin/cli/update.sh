# Sourced by cli.sh — do not execute directly.

update_menu() {
    show_banner
    echo -e "${BOLD}═══ Updates ═══${NC}"
    echo ""

    # Show current status
    echo -e "${CYAN}Checking for updates...${NC}"
    echo ""
    run_command "$SCRIPT_DIR/update.sh --check" "Update check"
    echo ""

    local options=(
        "Apply update (with rollback on failure)"
        "Apply update (with rollback + auto-env)"
        "Dry run (preview changes)"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "$SCRIPT_DIR/update.sh --rollback"
                ;;
            2)
                confirm_and_run "$SCRIPT_DIR/update.sh --rollback --auto-env"
                ;;
            3)
                run_command "$SCRIPT_DIR/update.sh --dry-run" "Dry run"
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