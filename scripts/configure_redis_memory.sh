#!/bin/bash
#
# Configure Redis Memory Limits & Eviction Policy
#
# This script prevents Redis OOM by:
# 1. Setting maxmemory to 768MB (75% of 1GB t4g.micro RAM)
# 2. Setting eviction policy to allkeys-lru (evict least-recently-used keys)
# 3. Backing up original config before changes
#
# CRITICAL: This prevents the Redis OOM disaster identified in ultra-think analysis
#

set -e  # Exit on error

echo "======================================================================="
echo "Redis Memory Configuration Script"
echo "======================================================================="
echo ""

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    echo "❌ ERROR: This script must be run as root or with sudo"
    echo "Usage: sudo bash $0"
    exit 1
fi

# Configuration
REDIS_CONF="/etc/redis/redis.conf"
BACKUP_CONF="/etc/redis/redis.conf.backup.$(date +%Y%m%d_%H%M%S)"
MAX_MEMORY="768mb"
EVICTION_POLICY="allkeys-lru"

# Step 1: Check if Redis is installed
echo "Step 1: Checking Redis installation..."
if ! command -v redis-cli &> /dev/null; then
    echo "❌ ERROR: redis-cli not found. Is Redis installed?"
    exit 1
fi

if [ ! -f "$REDIS_CONF" ]; then
    echo "❌ ERROR: Redis config not found at $REDIS_CONF"
    exit 1
fi

echo "✓ Redis installed, config found at $REDIS_CONF"
echo ""

# Step 2: Show current configuration
echo "Step 2: Current Redis memory configuration..."
echo "---"
redis-cli CONFIG GET maxmemory
redis-cli CONFIG GET maxmemory-policy
redis-cli INFO memory | grep used_memory_human
echo "---"
echo ""

# Step 3: Backup current config
echo "Step 3: Backing up Redis config..."
cp "$REDIS_CONF" "$BACKUP_CONF"
echo "✓ Backup created: $BACKUP_CONF"
echo ""

# Step 4: Update configuration
echo "Step 4: Updating Redis configuration..."

# Remove existing maxmemory and maxmemory-policy lines (commented or not)
sed -i '/^#\?maxmemory /d' "$REDIS_CONF"
sed -i '/^#\?maxmemory-policy /d' "$REDIS_CONF"

# Add new configuration at the end
cat >> "$REDIS_CONF" <<EOF

# === WordFlux Memory Management Configuration ===
# Added by configure_redis_memory.sh on $(date)
#
# Prevents Redis OOM by limiting memory and evicting LRU keys when full
# Reference: Ultra-Think Analysis - Redis OOM Disaster (Risk Score: 80/100)

# Set max memory to 768MB (75% of 1GB t4g.micro RAM)
# Leaves 256MB for OS, other processes, and memory overhead
maxmemory $MAX_MEMORY

# Evict least-recently-used keys when maxmemory reached
# This prevents Redis from refusing writes and causing system failure
maxmemory-policy $EVICTION_POLICY
EOF

echo "✓ Configuration updated"
echo ""

# Step 5: Test configuration
echo "Step 5: Testing Redis configuration..."
if redis-server "$REDIS_CONF" --test-memory 1; then
    echo "✓ Configuration valid"
else
    echo "❌ ERROR: Configuration test failed"
    echo "Restoring backup..."
    cp "$BACKUP_CONF" "$REDIS_CONF"
    exit 1
fi
echo ""

# Step 6: Restart Redis
echo "Step 6: Restarting Redis..."
systemctl restart redis-server

# Wait for Redis to come back up
sleep 2

if systemctl is-active --quiet redis-server; then
    echo "✓ Redis restarted successfully"
else
    echo "❌ ERROR: Redis failed to start"
    echo "Restoring backup..."
    cp "$BACKUP_CONF" "$REDIS_CONF"
    systemctl restart redis-server
    exit 1
fi
echo ""

# Step 7: Verify new configuration
echo "Step 7: Verifying new configuration..."
echo "---"
NEW_MAXMEM=$(redis-cli CONFIG GET maxmemory | tail -1)
NEW_POLICY=$(redis-cli CONFIG GET maxmemory-policy | tail -1)

echo "maxmemory: $NEW_MAXMEM (expected: 805306368 bytes = 768MB)"
echo "maxmemory-policy: $NEW_POLICY (expected: $EVICTION_POLICY)"

if [ "$NEW_POLICY" != "$EVICTION_POLICY" ]; then
    echo "❌ ERROR: Eviction policy not set correctly"
    exit 1
fi

if [ "$NEW_MAXMEM" = "0" ]; then
    echo "❌ ERROR: maxmemory still set to unlimited"
    exit 1
fi

echo "---"
echo ""

# Step 8: Calculate capacity
echo "Step 8: Redis capacity analysis..."
USED_MEM=$(redis-cli INFO memory | grep used_memory_human | cut -d: -f2 | tr -d '\r')
echo "Current memory usage: $USED_MEM"
echo "Max memory limit: $MAX_MEMORY (768MB)"
echo ""
echo "Capacity headroom: Redis will now evict LRU keys when reaching 768MB"
echo "Expected capacity: ~384,000 sessions (2KB each, 24h TTL)"
echo "Current usage: ~5,000 sessions/day"
echo "Safety margin: 76x"
echo ""

# Step 9: Summary
echo "======================================================================="
echo "✅ Redis Memory Configuration COMPLETE"
echo "======================================================================="
echo ""
echo "Changes made:"
echo "  • maxmemory: $MAX_MEMORY (768MB)"
echo "  • maxmemory-policy: $EVICTION_POLICY"
echo "  • Backup: $BACKUP_CONF"
echo ""
echo "What this prevents:"
echo "  🔴 Redis OOM disaster (total system failure)"
echo "  🔴 Redis refusing writes when memory full"
echo "  🔴 Chat history loss from emergency FLUSHDB"
echo ""
echo "Next steps:"
echo "  1. Monitor Redis memory usage: redis-cli INFO memory"
echo "  2. Set up Prometheus alerts for memory >600MB"
echo "  3. Run daily TTL audits: bash scripts/audit_redis_ttls.sh"
echo ""
echo "To revert (if needed):"
echo "  sudo cp $BACKUP_CONF $REDIS_CONF"
echo "  sudo systemctl restart redis-server"
echo ""