# Sourced by cli.sh — do not execute directly.

install_project() {
    show_banner
    echo -e "${BOLD}═══ Install / Setup Project ═══${NC}"
    echo ""

    local options=(
        "Full installation (all steps)"
        "Environment & .env configuration"
        "Cluster (multi-instance) setup"
        "Install dependencies (uv sync)"
        "Run migrations & system checks"
        "Set up cron jobs"
        "Set up shell aliases"
        "Deploy (Docker / systemd)"
        "Check installation status"
        "Set production mode"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)  run_command "$SCRIPT_DIR/install.sh" "Full installation" ;;
            2)  run_command "$SCRIPT_DIR/install.sh env" "Environment setup" ;;
            3)  run_command "$SCRIPT_DIR/install.sh cluster" "Cluster setup" ;;
            4)  run_command "$SCRIPT_DIR/install.sh deps" "Installing dependencies" ;;
            5)  run_command "$SCRIPT_DIR/install.sh migrate" "Migrations & checks" ;;
            6)  run_command "$SCRIPT_DIR/install.sh cron" "Cron setup" ;;
            7)  run_command "$SCRIPT_DIR/install.sh aliases" "Shell aliases" ;;
            8)  run_command "$SCRIPT_DIR/install.sh deploy" "Deployment" ;;
            9)  check_installation ;;
            10) confirm_and_run "$SCRIPT_DIR/set_production.sh" ;;
            11) return ;;
            *)  echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}

check_installation() {
    source "$SCRIPT_DIR/lib/health_check.sh"
    run_all_checks
}