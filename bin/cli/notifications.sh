# shellcheck shell=bash
# Sourced by cli.sh — do not execute directly.

notify_menu() {
    while true; do
        show_banner
        if tuin_menu "Notifications" \
            "test_notify - Send a test notification"
        then
            case $TUIN_REPLY in
                "test_notify - Send a test notification")
                    test_notify_menu ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}

test_notify_menu() {
    while true; do
        show_banner
        tuin_section "test_notify"
        echo "Send a test notification to verify driver configuration."
        echo ""
        if tuin_menu "test_notify" \
            "Interactive wizard (recommended)" \
            "Non-interactive (specify driver and flags)"
        then
            case $TUIN_REPLY in
                "Interactive wizard (recommended)")
                    confirm_and_run "uv run python manage.py test_notify" ;;
                "Non-interactive (specify driver and flags)")
                    test_notify_non_interactive ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}

test_notify_non_interactive() {
    local driver_name
    driver_name=$(tuin_input "Enter driver name (email/slack/pagerduty/generic)")
    if [ -z "$driver_name" ]; then
        echo -e "${RED}Driver name required${NC}"
        return
    fi

    local channel message
    channel=$(tuin_input "Enter channel (optional)")
    message=$(tuin_input "Enter custom message (optional)")

    local cmd="uv run python manage.py test_notify $driver_name --non-interactive"
    if [ -n "$channel" ]; then
        cmd="$cmd --channel=$channel"
    fi
    if [ -n "$message" ]; then
        cmd="$cmd --message=\"$message\""
    fi

    confirm_and_run "$cmd"
}