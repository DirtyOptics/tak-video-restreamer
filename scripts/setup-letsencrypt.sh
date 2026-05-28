#!/bin/bash
#
# This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
# Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.
#
# This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
# This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/
#
# Let's Encrypt Certificate Setup Script
# Automates obtaining and installing Let's Encrypt certificates
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Script configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CERTS_DIR="$PROJECT_DIR/data/certs"

echo -e "${GREEN}Let's Encrypt Certificate Setup${NC}"
echo "=================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}Note: This script may require sudo for some operations${NC}"
fi

# Check if certbot is installed
if ! command -v certbot &> /dev/null; then
    echo -e "${RED}Error: certbot is not installed${NC}"
    echo ""
    echo "Install certbot:"
    echo "  Ubuntu/Debian: sudo apt-get install certbot"
    echo "  CentOS/RHEL:   sudo yum install certbot"
    echo "  macOS:         brew install certbot"
    exit 1
fi

echo "Certbot version: $(certbot --version)"
echo ""

# Get domain name
read -p "Enter your domain name (e.g., media.example.com): " DOMAIN
if [ -z "$DOMAIN" ]; then
    echo -e "${RED}Error: Domain name is required${NC}"
    exit 1
fi

# Get email
read -p "Enter your email address for Let's Encrypt notifications: " EMAIL
if [ -z "$EMAIL" ]; then
    echo -e "${RED}Error: Email address is required${NC}"
    exit 1
fi

# Choose method
echo ""
echo "Select certificate acquisition method:"
echo "  1) Standalone (requires port 80, stops container temporarily)"
echo "  2) DNS Challenge (works behind firewall, requires DNS API)"
echo "  3) Webroot (requires running web server on port 80)"
read -p "Choice [1-3]: " METHOD

case $METHOD in
    1)
        echo ""
        echo -e "${YELLOW}Using standalone mode - stopping Docker container...${NC}"
        cd "$PROJECT_DIR"
        docker-compose down
        
        echo "Obtaining certificate from Let's Encrypt..."
        sudo certbot certonly --standalone \
            -d "$DOMAIN" \
            --email "$EMAIL" \
            --agree-tos \
            --non-interactive
        
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to obtain certificate${NC}"
            docker-compose up -d
            exit 1
        fi
        ;;
    
    2)
        echo ""
        echo "DNS Challenge requires a DNS provider plugin."
        echo "Available plugins:"
        echo "  - certbot-dns-cloudflare"
        echo "  - certbot-dns-route53 (AWS)"
        echo "  - certbot-dns-google"
        echo "  - certbot-dns-digitalocean"
        echo ""
        read -p "Enter DNS plugin name (e.g., cloudflare): " DNS_PLUGIN
        
        if [ -z "$DNS_PLUGIN" ]; then
            echo -e "${RED}Error: DNS plugin name is required${NC}"
            exit 1
        fi
        
        read -p "Enter path to credentials file: " CREDS_FILE
        if [ ! -f "$CREDS_FILE" ]; then
            echo -e "${RED}Error: Credentials file not found: $CREDS_FILE${NC}"
            exit 1
        fi
        
        echo "Obtaining certificate from Let's Encrypt..."
        sudo certbot certonly --dns-$DNS_PLUGIN \
            --dns-${DNS_PLUGIN}-credentials "$CREDS_FILE" \
            -d "$DOMAIN" \
            --email "$EMAIL" \
            --agree-tos \
            --non-interactive
        
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to obtain certificate${NC}"
            exit 1
        fi
        ;;
    
    3)
        echo ""
        read -p "Enter webroot path (e.g., /var/www/html): " WEBROOT
        if [ ! -d "$WEBROOT" ]; then
            echo -e "${RED}Error: Webroot directory not found: $WEBROOT${NC}"
            exit 1
        fi
        
        echo "Obtaining certificate from Let's Encrypt..."
        sudo certbot certonly --webroot \
            -w "$WEBROOT" \
            -d "$DOMAIN" \
            --email "$EMAIL" \
            --agree-tos \
            --non-interactive
        
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to obtain certificate${NC}"
            exit 1
        fi
        ;;
    
    *)
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac

# Copy certificates
echo ""
echo "Copying certificates to project directory..."
mkdir -p "$CERTS_DIR"

sudo cp "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$CERTS_DIR/server.crt"
sudo cp "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$CERTS_DIR/server.key"

# Fix ownership and permissions
sudo chown $USER:$USER "$CERTS_DIR/server.crt" "$CERTS_DIR/server.key"
chmod 644 "$CERTS_DIR/server.crt"
chmod 600 "$CERTS_DIR/server.key"

echo -e "${GREEN}✓ Certificates installed successfully${NC}"
echo ""
echo "Certificate files:"
echo "  - $CERTS_DIR/server.crt"
echo "  - $CERTS_DIR/server.key"
echo ""

# Restart container if it was running
if [ "$METHOD" = "1" ]; then
    echo "Restarting Docker container..."
    cd "$PROJECT_DIR"
    docker-compose up -d
    echo -e "${GREEN}✓ Container restarted${NC}"
fi

# Create renewal script
RENEWAL_SCRIPT="/usr/local/bin/renew-media-certs-$DOMAIN.sh"
echo ""
echo "Creating automatic renewal script..."

sudo tee "$RENEWAL_SCRIPT" > /dev/null << EOF
#!/bin/bash
# Auto-generated renewal script for $DOMAIN
certbot renew --quiet
if [ \$? -eq 0 ]; then
    cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem $CERTS_DIR/server.crt
    cp /etc/letsencrypt/live/$DOMAIN/privkey.pem $CERTS_DIR/server.key
    chmod 644 $CERTS_DIR/server.crt
    chmod 600 $CERTS_DIR/server.key
    cd $PROJECT_DIR && docker-compose restart
    echo "Certificates renewed and container restarted"
fi
EOF

sudo chmod +x "$RENEWAL_SCRIPT"

echo -e "${GREEN}✓ Renewal script created: $RENEWAL_SCRIPT${NC}"
echo ""
echo "To set up automatic renewal (runs daily at 2 AM):"
echo "  sudo crontab -e"
echo "  Add this line:"
echo "  0 2 * * * $RENEWAL_SCRIPT"
echo ""
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "Verify installation:"
echo "  1. Go to http://localhost:3000/settings"
echo "  2. Check TLS/RTSPS Settings section"
echo "  3. Should show: ✓ Custom certificates installed"
echo "  4. Issuer should be Let's Encrypt"
echo ""
echo "Test RTSPS connection:"
echo "  ffplay rtsps://$DOMAIN:8555/your-stream"
