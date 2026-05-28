# Sourced by cli.sh — do not execute directly.

cluster_menu() {
    show_banner
    echo -e "${BOLD}═══ Cluster ═══${NC}"
    echo ""
    echo "Manage cluster log-push destinations and run push operations."
    echo ""

    local options=(
        "Add destination"
        "List destinations"
        "Show destination details"
        "Remove destination"
        "Enable / disable destination"
        "Set forward-received policy"
        "Test destination (doctor)"
        "Cluster status               (PR 2 — not yet implemented)"
        "Push logs now (manual)       (PR 2 — not yet implemented)"
        "Alerts: push to hub"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)  _cluster_dest_add_prompt ;;
            2)  confirm_and_run "uv run python manage.py cluster_dest_list" ;;
            3)  _cluster_dest_positional_prompt cluster_dest_show ;;
            4)  _cluster_dest_remove_prompt ;;
            5)  _cluster_dest_named_prompt cluster_dest_toggle ;;
            6)  _cluster_dest_forward_prompt ;;
            7)  _cluster_dest_positional_prompt cluster_dest_doctor ;;
            8)  echo -e "${YELLOW}Cluster status lands in PR 2 — not yet implemented.${NC}" ;;
            9)  echo -e "${YELLOW}Manual log push lands in PR 2 — not yet implemented.${NC}" ;;
            10) _cluster_push_to_hub_menu ;;
            11) return ;;
            *)  echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}

# ----------------------------------------------------------------------------
# Sub-prompts for the cluster_dest_* commands.
#
# All user input is shell-quoted via printf %q before being handed to
# confirm_and_run (which eval's the string). Without quoting, a name like
# `foo; rm -rf ~` would inject a separate command.
# ----------------------------------------------------------------------------

_cluster_dest_add_prompt() {
    local name url api_key streams forward
    read -rp "Destination name: " name
    if [ -z "$name" ]; then
        echo -e "${RED}Name required${NC}"
        return
    fi
    read -rp "Hub URL (e.g. https://hub.example.com): " url
    if [ -z "$url" ]; then
        echo -e "${RED}URL required${NC}"
        return
    fi
    read -rp "API key name: " api_key
    if [ -z "$api_key" ]; then
        echo -e "${RED}API key required${NC}"
        return
    fi
    read -rp "Streams (default: events,heartbeats): " streams
    read -rp "Also forward records received from other agents? (y/N): " forward

    local cmd
    printf -v cmd 'uv run python manage.py cluster_dest_add --name %q --url %q --api-key %q' \
        "$name" "$url" "$api_key"
    if [ -n "$streams" ]; then
        printf -v cmd '%s --streams %q' "$cmd" "$streams"
    fi
    if [[ "$forward" =~ ^[Yy]$ ]]; then
        cmd="$cmd --forward"
    fi
    confirm_and_run "$cmd"
}

_cluster_dest_remove_prompt() {
    local name hard
    read -rp "Destination name: " name
    if [ -z "$name" ]; then
        echo -e "${RED}Name required${NC}"
        return
    fi
    read -rp "Hard delete (drop the row entirely)? (y/N): " hard

    local cmd
    printf -v cmd 'uv run python manage.py cluster_dest_remove --name %q' "$name"
    if [[ "$hard" =~ ^[Yy]$ ]]; then
        cmd="$cmd --hard"
    fi
    confirm_and_run "$cmd"
}

_cluster_dest_forward_prompt() {
    local name state
    read -rp "Destination name: " name
    if [ -z "$name" ]; then
        echo -e "${RED}Name required${NC}"
        return
    fi
    read -rp "Forward-received state (on/off): " state
    if [ "$state" != "on" ] && [ "$state" != "off" ]; then
        echo -e "${RED}State must be 'on' or 'off'${NC}"
        return
    fi
    local cmd
    printf -v cmd 'uv run python manage.py cluster_dest_forward --name %q %s' "$name" "$state"
    confirm_and_run "$cmd"
}

_cluster_dest_named_prompt() {
    # For commands that take --name (e.g. toggle).
    local subcmd="$1"
    local name
    read -rp "Destination name: " name
    if [ -z "$name" ]; then
        echo -e "${RED}Name required${NC}"
        return
    fi
    local cmd
    printf -v cmd 'uv run python manage.py %s --name %q' "$subcmd" "$name"
    confirm_and_run "$cmd"
}

_cluster_dest_positional_prompt() {
    # For commands that take a positional name (e.g. show, doctor).
    local subcmd="$1"
    local name
    read -rp "Destination name: " name
    if [ -z "$name" ]; then
        echo -e "${RED}Name required${NC}"
        return
    fi
    local cmd
    printf -v cmd 'uv run python manage.py %s %q' "$subcmd" "$name"
    confirm_and_run "$cmd"
}

_cluster_push_to_hub_menu() {
    echo ""
    echo -e "${BOLD}Push checks to hub${NC}"
    echo ""
    local options=(
        "Push checks to hub"
        "Push checks to hub (dry run)"
        "Push checks to hub (specific checkers)"
        "Back"
    )
    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1) confirm_and_run "uv run python manage.py push_to_hub" ;;
            2) confirm_and_run "uv run python manage.py push_to_hub --dry-run" ;;
            3)
                local checker_names cmd
                read -rp "Enter checker names (comma-separated): " checker_names
                if [ -n "$checker_names" ]; then
                    printf -v cmd 'uv run python manage.py push_to_hub --checkers %q' \
                        "$checker_names"
                    confirm_and_run "$cmd"
                else
                    echo -e "${RED}Checker names required${NC}"
                fi
                ;;
            4) return ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}