#!/bin/bash
echo "🚀 Installing Chrome..."
apt-get update
apt-get install -y wget gnupg unzip
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list
apt-get update
apt-get install -y google-chrome-stable
CHROME_VERSION=$(google-chrome --version | awk '{print $3}')
wget -N "https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/${CHROME_VERSION}/linux64/chromedriver-linux64.zip"
unzip -o chromedriver-linux64.zip
mv chromedriver-linux64/chromedriver /usr/local/bin/
chmod +x /usr/local/bin/chromedriver
echo "✅ Chrome: $(google-chrome --version)"
echo "✅ ChromeDriver: $(/usr/local/bin/chromedriver --version)"
pip install -r requirements.txt
echo "✅ Build completed!"