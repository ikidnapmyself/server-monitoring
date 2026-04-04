# Sourced by cli.sh — do not execute directly.

install_project() {
    show_banner
    echo -e "${BOLD}═══ Install / Setup Project ═══${NC}"
    echo ""

    local options=(
        "Full installation (uv sync + pre-commit)"
        "Install dependencies only (uv sync)"
        "Install pre-commit hooks"
        "Setup shell aliases"
        "Check installation status"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                echo -e "${YELLOW}Running full installation...${NC}"
                run_command "uv sync" "Installing dependencies"
                run_command "uv run pre-commit install" "Installing pre-commit hooks"
                ;;
            2)
                run_command "uv sync" "Installing dependencies"
                ;;
            3)
                run_command "uv run pre-commit install" "Installing pre-commit hooks"
                ;;
            4)
                run_command "$SCRIPT_DIR/install.sh aliases" "Setting up shell aliases"
                ;;
            5)
                check_installation
                ;;
            6)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

check_installation() {
    source "$SCRIPT_DIR/lib/health_check.sh"
    run_all_checks
}
