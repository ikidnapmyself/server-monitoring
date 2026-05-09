"""Shared cleanup-advice rules referenced by disk analyzers.

Each rule is (path-keywords, [title, *detail_lines]). Subclasses
include only the rules whose keywords might appear in their declared
scan_targets.
"""

# Cross-platform development tooling
PIP = (["pip"], ["Clear pip cache:", "pip cache purge"])
NPM = (["npm", ".npm"], ["Clear npm cache:", "npm cache clean --force"])
YARN = (["yarn", "Yarn"], ["Clear Yarn cache:", "yarn cache clean"])
PNPM = (["pnpm"], ["Prune pnpm content-addressable store:", "pnpm store prune"])
COMPOSER = (["composer"], ["Clear Composer cache:", "composer clear-cache"])
GRADLE = (
    ["gradle", ".gradle"],
    [
        "Clear old Gradle caches:",
        "Stop the Gradle daemon: gradle --stop",
        "Then delete ~/.gradle/caches/<old version>",
    ],
)
MAVEN = (
    ["maven", ".m2"],
    [
        "Clear stale Maven artifacts:",
        "mvn dependency:purge-local-repository (re-resolves managed deps)",
        "Or delete old SNAPSHOTs only: find ~/.m2/repository -name '*-SNAPSHOT' -mtime +90 -exec rm -rf {} +",
    ],
)
CARGO = (
    ["cargo", ".cargo"],
    [
        "Clean Rust cargo cache:",
        "cargo install cargo-cache && cargo cache --autoclean",
    ],
)
GO_MODULES = (
    ["go/pkg", "GOPATH"],
    [
        "Clean Go module cache:",
        "go clean -modcache",
    ],
)
JETBRAINS = (
    ["JetBrains"],
    [
        "Invalidate JetBrains IDE caches:",
        "In the IDE: File → Invalidate Caches and Restart",
        "Or delete ~/Library/Caches/JetBrains/<product> for products you no longer use",
    ],
)

# macOS-specific
HOMEBREW = (
    ["Homebrew"],
    [
        "Free Homebrew cache:",
        "brew cleanup --prune=all",
    ],
)
XCODE = (
    ["DerivedData", "Xcode"],
    [
        "Remove Xcode DerivedData (safe; rebuilt on next build):",
        "rm -rf ~/Library/Developer/Xcode/DerivedData",
    ],
)
APPLE_CACHES = (
    ["Caches"],
    [
        "Clear application caches under ~/Library/Caches (review per-app first)",
    ],
)

# Linux-specific
APT = (["apt"], ["Clear APT package cache:", "sudo apt clean"])
JOURNAL = (
    ["journal"],
    [
        "Trim systemd journal logs:",
        "sudo journalctl --vacuum-size=100M",
    ],
)
DOCKER = (
    ["docker"],
    [
        "Clean unused Docker data (containers, images, networks, build cache):",
        "docker system prune",
        "Add --volumes to also remove unused volumes (destructive)",
    ],
)
SNAP = (
    ["snap"],
    [
        "Remove old snap package revisions:",
        "snap list --all | awk '/disabled/{print $1, $3}' | "
        "xargs -L 1 sudo snap remove --revision",
    ],
)

# Cross-platform system targets
LOG_ROTATE = (
    ["/var/log"],
    [
        "Compress or rotate large logs in /var/log",
        "Most distributions handle this with logrotate; check /etc/logrotate.d",
    ],
)
USER_CACHE = (
    [".cache"],
    [
        "Clear user caches in ~/.cache (review per-app first)",
    ],
)
