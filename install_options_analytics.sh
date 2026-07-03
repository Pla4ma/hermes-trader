#!/bin/bash
# Quick install script for options analytics libraries
# Run from /opt/hermes-trader

echo "📦 Installing Options Analytics Stack..."

# Core Greeks & Pricing
pip install py_vollib 2>/dev/null || pip3 install py_vollib
pip install py_vollib_vectorized 2>/dev/null || pip3 install py_vollib_vectorized
pip install optionlab 2>/dev/null || pip3 install optionlab
pip install mibian 2>/dev/null || pip3 install mibian

# Volatility Surface  
pip install pysabr 2>/dev/null || pip3 install pysabr
pip install QuantLib 2>/dev/null || pip3 install QuantLib

# Portfolio & Risk
pip install riskfolio-lib 2>/dev/null || pip3 install riskfolio-lib

# Data & Visualization
pip install openbb 2>/dev/null || pip3 install openbb

echo "✅ Core libraries installed"
echo ""
echo "📊 Key repositories to clone:"
echo "  git clone https://github.com/marketcalls/opengreeks.git    # Fast Greeks (Rust)"
echo "  git clone https://github.com/Matteo-Ferrara/gex-tracker.git # GEX tracking"
echo "  git clone https://github.com/rgaveiga/optionlab.git         # Strategy evaluation"
echo "  git clone https://github.com/vollib/py_vollib.git           # IV solving"
echo "  git clone https://github.com/FlashAlpha-lab/flashalpha-examples.git  # GEX/flow examples"
echo ""
echo "🔗 Essential URLs:"
echo "  Awesome list: https://github.com/FlashAlpha-lab/awesome-options-analytics"
echo "  FlashAlpha:   https://flashalpha.com (GEX, flow, VRP API)"
echo "  OpenBB:       https://openbb.co (open-source terminal)"
