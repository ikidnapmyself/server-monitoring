# Sourced by cli.sh — do not execute directly.

notify_menu() {
    show_banner
    echo -e "${BOLD}═══ Notifications ═══${NC}"
    echo ""

    local options=(
        "test_notify - Send a test notification"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                test_notify_menu
                ;;
            2)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

test_notify_menu() {
    show_banner
    echo -e "${BOLD}═══ test_notify ═══${NC}"
    echo ""
    echo "Send a test notification to verify driver configuration"
    echo ""

    local options=(
        "Interactive wizard (recommended)"
        "Non-interactive (specify driver and flags)"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py test_notify"
                ;;
            2)
                test_notify_non_interactive
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

test_notify_non_interactive() {
    echo ""
    echo -e "${BOLD}Available drivers:${NC} email, slack, pagerduty, generic"
    echo ""

    read -p "Enter driver name: " driver_name
    if [ -z "$driver_name" ]; then
        echo -e "${RED}Driver name required${NC}"
        return
    fi

    read -p "Enter channel (optional): " channel
    read -p "Enter custom message (optional): " message

    local cmd="uv run python manage.py test_notify $driver_name --non-interactive"
    if [ -n "$channel" ]; then
        cmd="$cmd --channel=$channel"
    fi
    if [ -n "$message" ]; then
        cmd="$cmd --message=\"$message\""
    fi

    confirm_and_run "$cmd"
}