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
                run_command "$SCRIPT_DIR/setup_aliases.sh" "Setting up shell aliases"
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
    echo -e "${BOLD}Checking installation status...${NC}"
    echo ""

    # Check uv
    if command -v uv &> /dev/null; then
        echo -e "${GREEN}✓${NC} uv is installed ($(uv --version))"
    else
        echo -e "${RED}✗${NC} uv is not installed"
    fi

    # Check .venv
    if [ -d ".venv" ]; then
        echo -e "${GREEN}✓${NC} Virtual environment exists"
    else
        echo -e "${RED}✗${NC} Virtual environment not found"
    fi

    # Check pre-commit
    if [ -f ".git/hooks/pre-commit" ]; then
        echo -e "${GREEN}✓${NC} Pre-commit hooks installed"
    else
        echo -e "${YELLOW}!${NC} Pre-commit hooks not installed"
    fi

    # Check aliases
    if [ -f "$SCRIPT_DIR/aliases.sh" ]; then
        echo -e "${GREEN}✓${NC} Shell aliases configured"
    else
        echo -e "${YELLOW}!${NC} Shell aliases not configured (run bin/setup_aliases.sh)"
    fi

    # Check Django
    if uv run python manage.py check &> /dev/null; then
        echo -e "${GREEN}✓${NC} Django is configured correctly"
    else
        echo -e "${RED}✗${NC} Django check failed"
    fi
}
