# IV (Implied Volatility) Surface Construction for Options Trading

## Table of Contents

1. [Overview](#overview)
2. [SVI Parametrization (Stochastic Volatility Inspired)](#svi-parametrization)
3. [SSVI (Surface SVI)](#ssvi-surface-svi)
4. [SABR Model](#sabr-model)
5. [Open-Source Implementations](#open-source-implementations)
6. [Alpaca Options Chain Integration](#alpaca-options-chain)
7. [IV Surface Construction Pipeline](#iv-surface-construction-pipeline)
8. [Trading Signals from IV Surface](#trading-signals-from-iv-surface)
9. [Skew/Smile Term Structure Analysis](#skew-smile-term-structure)
10. [Institutional Best Practices](#institutional-best-practices)
11. [Key References](#key-references)

---

## Overview

An **Implied Volatility (IV) Surface** is a 2D plot showing the implied volatility of options across:
- **X-axis**: Moneyness (or Strike / Spot ratio)
- **Y-axis**: Time to Expiry (TTE)
- **Z-axis**: Implied Volatility (σ)

The surface captures the **volatility smile/skew** (strike dimension) and **term structure** (time dimension). Fitting a smooth parametric surface to discrete market observations is critical for:
- Pricing exotic options and interpolating missing quotes
- Hedging (computing Greeks consistently across strikes/expiries)
- Identifying trading opportunities (relative value, vol arbitrage)
- Risk management (portfolio stress testing)

---

## SVI Parametrization

### Original SVI Formula (Gatheral, 2004)

The **SVI (Stochastic Volatility Inspired)** parametrization gives the **total implied variance** as a function of log-moneyness `k = ln(K/F)` for a **single expiry T**:

```
w(k; a, b, ρ, m, σ) = a + b * (ρ * (k - m) + sqrt((k - m)² + σ²))
```

Where:
- `w(k)` = total implied variance = σ²_BS(k) * T
- `k = ln(K / F)` where K is strike and F is forward price
- `a` = overall level of variance (vertical shift)
- `b` = rotation angle of the wings (controls smile steepness)
- `ρ` = correlation between stock and vol (controls skew/asymmetry, typically negative for equities: -1 < ρ < 1)
- `m` = translation parameter (shifts the smile left/right)
- `σ` = controls the curvature/width of the smile

**Parameter constraints** for arbitrage-free smiles:
```
b ≥ 0
0 ≤ ρ ≤ 1  (some implementations: -1 < ρ < 1)
σ > 0
a + b * σ * sqrt(1 - ρ²) ≥ 0  (ensures non-negative variance)
```

### SVI in Terms of Implied Volatility

To get implied volatility from total variance:
```
σ_BS(k) = sqrt(w(k) / T)
```

### Fitting SVI to Market Data

**Step 1**: Convert market option prices to implied volatilities using Black-Scholes:
```python
from scipy.stats import norm
import numpy as np

def bs_implied_vol(price, F, K, T, option_type='call'):
    """Compute BS implied volatility using Brent's method."""
    from scipy.optimize import brentq
    
    def bs_price(sigma, F, K, T, option_type):
        d1 = (np.log(F / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        if option_type == 'call':
            return F * norm.cdf(d1) - K * norm.cdf(d2)
        else:
            return K * norm.cdf(-d2) - F * norm.cdf(-d1)
    
    def objective(sigma):
        return bs_price(sigma, F, K, T, option_type) - price
    
    return brentq(objective, 1e-6, 5.0)
```

**Step 2**: Convert implied volatilities to total variances:
```python
def iv_to_total_var(iv, T):
    return iv**2 * T
```

**Step 3**: Fit SVI parameters using constrained optimization:
```python
from scipy.optimize import minimize

def svi_total_variance(k, a, b, rho, m, sigma):
    """SVI formula for total implied variance."""
    return a + b * (rho * (k - m) + np.sqrt((k - m)**2 + sigma**2))

def svi_fit(log_moneyness, total_variances, initial_guess=None):
    """
    Fit SVI parameters to market total variance observations.
    
    Parameters:
        log_moneyness: array of ln(K/F) values
        total_variances: array of σ²_BS * T values
        initial_guess: (a, b, rho, m, sigma) or None for default
    
    Returns:
        Optimal SVI parameters (a, b, rho, m, sigma)
    """
    if initial_guess is None:
        # Initial guess based on data characteristics
        w_min = np.min(total_variances)
        k_at_min = log_moneyness[np.argmin(total_variances)]
        initial_guess = [w_min, 0.1, -0.3, k_at_min, 0.3]
    
    def objective(params):
        a, b, rho, m, sigma = params
        fitted = svi_total_variance(log_moneyness, a, b, rho, m, sigma)
        return np.sum((fitted - total_variances)**2)
    
    # Constraints for arbitrage-free SVI
    constraints = [
        {'type': 'ineq', 'fun': lambda p: p[1]},  # b >= 0
        {'type': 'ineq', 'fun': lambda p: p[2] + 1},  # rho >= -1
        {'type': 'ineq', 'fun': lambda p: 1 - p[2]},   # rho <= 1
        {'type': 'ineq', 'fun': lambda p: p[4]},        # sigma > 0
        # Non-negative variance at the money
        {'type': 'ineq', 'fun': lambda p: p[0] + p[1] * p[4] * np.sqrt(1 - p[2]**2)},
    ]
    
    bounds = [(None, None), (0, None), (-0.999, 0.999), (None, None), (1e-6, None)]
    
    result = minimize(objective, initial_guess, method='SLSQP',
                      bounds=bounds, constraints=constraints)
    
    return result.x
```

### Example Usage

```python
import numpy as np

# Sample market data: strikes, implied vols, and expiry
strikes = np.array([90, 95, 100, 105, 110])
implied_vols = np.array([0.28, 0.24, 0.22, 0.21, 0.23])
F = 100  # Forward price
T = 0.25  # 3 months to expiry

# Convert to log-moneyness and total variance
log_moneyness = np.log(strikes / F)
total_variances = implied_vols**2 * T

# Fit SVI
params = svi_fit(log_moneyness, total_variances)
a, b, rho, m, sigma = params

print(f"SVI Parameters: a={a:.4f}, b={b:.4f}, ρ={rho:.4f}, m={m:.4f}, σ={sigma:.4f}")

# Generate smooth implied volatility curve
k_grid = np.linspace(-0.3, 0.3, 100)
fitted_w = svi_total_variance(k_grid, a, b, rho, m, sigma)
fitted_iv = np.sqrt(fitted_w / T)
```

---

## SSVI (Surface SVI)

### SSVI Formula (Gatheral & Jacquier, 2014)

SSVI extends SVI to be **arbitrage-free across expiries** by parametrizing the total implied variance surface:

```
w(k, T) = θ(T) * [1 + φ(T) * ρ * (k / θ(T)) + sqrt((k / θ(T))² - 2 * ρ * (k / θ(T)) + 1)]
```

Where:
- `θ(T)` = ATM total variance as a function of expiry (term structure)
- `φ(T)` = smile parameter (controls smile width at each expiry)
- `ρ` = correlation parameter (shared across expiries, typically negative)

**Key properties:**
- `w(k, 0) = 0` (no variance at T=0)
- `θ(T) > 0` for all T > 0
- `0 < φ(T) < 1` (ensures arbitrage-free)
- `-1 < ρ < 1`
- `θ(T)` is typically increasing in T (normal term structure)

### SSVI Arbitrage-Free Conditions

For no calendar spread arbitrage:
```
dθ/dT ≥ 0
```

For no butterfly spread arbitrage:
```
∂²w/∂k² ≥ 0
```

### Fitting SSVI

```python
import numpy as np
from scipy.optimize import minimize

def ssvi_total_variance(k, T, theta_T, phi_T, rho):
    """
    SSVI total implied variance.
    
    Parameters:
        k: log-moneyness ln(K/F)
        T: time to expiry
        theta_T: ATM total variance at expiry T
        phi_T: smile parameter at expiry T
        rho: correlation parameter (shared across expiries)
    """
    k_star = k / np.sqrt(theta_T)
    return theta_T * (1 + phi_T * rho * k_star + np.sqrt(k_star**2 - 2 * rho * k_star + 1))

def theta_T_parametric(T, theta_params):
    """
    Parametric form for ATM total variance term structure.
    Common form: θ(T) = a*T + b*T^2
    """
    a, b = theta_params
    return a * T + b * T**2

def phi_T_parametric(T, phi_params):
    """
    Parametric form for smile parameter.
    Common form: φ(T) = c / (1 + d*T)
    """
    c, d = phi_params
    return c / (1 + d * T)
```

### Surface Fitting Algorithm

```python
def fit_ssvi_surface(strikes, expiries, implied_vols, forwards):
    """
    Fit SSVI surface to market data.
    
    Parameters:
        strikes: 2D array (n_expiries, n_strikes)
        expiries: array of time-to-expiry values
        implied_vols: 2D array (n_expiries, n_strikes)
        forwards: array of forward prices per expiry
    
    Returns:
        Fitted SSVI parameters
    """
    # Collect all observations
    all_k = []
    all_T = []
    all_w = []
    
    for i, T in enumerate(expiries):
        F = forwards[i]
        k = np.log(strikes[i] / F)
        w = implied_vols[i]**2 * T
        all_k.extend(k)
        all_T.extend([T] * len(k))
        all_w.extend(w)
    
    all_k = np.array(all_k)
    all_T = np.array(all_T)
    all_w = np.array(all_w)
    
    def objective(params):
        rho, theta_params, phi_params = params[0], params[1:3], params[3:5]
        fitted_w = np.array([
            ssvi_total_variance(k, T, theta_T_parametric(T, theta_params), 
                               phi_T_parametric(T, phi_params), rho)
            for k, T in zip(all_k, all_T)
        ])
        return np.sum((fitted_w - all_w)**2)
    
    # Initial guess
    theta0 = [0.04, 0.1]  # θ(T) = 0.04*T + 0.1*T²
    phi0 = [0.5, 1.0]     # φ(T) = 0.5 / (1 + T)
    rho0 = -0.3
    
    initial_params = np.array([rho0] + theta0 + phi0)
    
    result = minimize(objective, initial_params, method='L-BFGS-B',
                      bounds=[(-0.99, 0.99), (0, None), (0, None), 
                              (0, None), (0, None)])
    
    return result.x
```

---

## SABR Model

### SABR Formula (Hagan et al., 2002)

SABR (Stochastic Alpha, Beta, Rho) is the industry-standard parametrization for interest rate smiles:

```
σ_B(K, F) ≈ σ_0 * (z / x(z)) * 
    [1 + ((2γ₃ - 5γ₂²)/24 * σ_0² + ραβγ₂/4 + (2-3ρ²)/24 * α²) * T]
```

Where:
- `F` = forward rate
- `K` = strike
- `α` = volatility of volatility (vol-of-vol)
- `β` = elasticity parameter (0 = normal, 1 = lognormal)
- `ρ` = correlation between forward and vol
- `σ_0` = ATM volatility
- `z = (α/σ_0) * (F^(1-β) - K^(1-β)) / (1-β)`
- `x(z) = ln((sqrt(1 - 2ρz + z²) + z - ρ) / (1 - ρ))`
- `γ₁ = β / F`
- `γ₂ = -β(β-1) / F²`

### SABR Parameter Interpretation

| Parameter | Typical Range | Effect |
|-----------|---------------|--------|
| α (vol-of-vol) | 0.1 - 1.0 | Controls smile curvature |
| β (elasticity) | 0.0 - 1.0 | 0=Normal, 0.5=CEV, 1=Lognormal |
| ρ (correlation) | -0.9 - 0.0 | Controls skew direction |
| σ₀ (ATM vol) | Market input | Center of smile |

### SABR Calibration

```python
import numpy as np
from scipy.optimize import least_squares

def sabr_implied_vol(F, K, T, alpha, beta, rho, sigma_0):
    """
    SABR implied volatility approximation (Hagan et al. 2002).
    """
    # Handle ATM case
    if np.abs(F - K) < 1e-10:
        term1 = sigma_0 * (1 + ((2*gamma_3 - 5*gamma_2**2)/24 * sigma_0**2 + 
               rho*alpha*beta*gamma_2/4 + (2-3*rho**2)/24 * alpha**2) * T)
        return term1
    
    F_K = F * K
    ln_F_K = np.log(F / K)
    
    z = (alpha / sigma_0) * (F**(1-beta) - K**(1-beta)) / (1 - beta)
    
    x_z = np.log((np.sqrt(1 - 2*rho*z + z**2) + z - rho) / (1 - rho))
    
    gamma_1 = beta / F
    gamma_2 = -beta * (beta - 1) / F**2
    
    sigma_0_approx = sigma_0  # Initial guess
    
    # First-order correction
    term1 = sigma_0
    
    # Second-order correction
    term2 = 1 + (
        (2*gamma_3 - 5*gamma_2**2) / 24 * sigma_0**2 +
        rho * alpha * beta * gamma_2 / 4 +
        (2 - 3*rho**2) / 24 * alpha**2
    ) * T
    
    # Full approximation
    if np.abs(z) > 1e-10:
        result = alpha * z / x_z * term2
    else:
        result = sigma_0 * term2
    
    return result

def sabr_calibrate(strikes, market_vols, F, T, beta=0.5):
    """
    Calibrate SABR parameters to market implied volatilities.
    
    Parameters:
        strikes: array of strike prices
        market_vols: array of market implied volatilities
        F: forward price
        T: time to expiry
        beta: elasticity parameter (fixed)
    
    Returns:
        Calibrated (alpha, rho, sigma_0) parameters
    """
    def residuals(params):
        alpha, rho, sigma_0 = params
        model_vols = [sabr_implied_vol(F, K, T, alpha, beta, rho, sigma_0) 
                      for K in strikes]
        return np.array(model_vols) - np.array(market_vols)
    
    # Initial guess
    atm_idx = np.argmin(np.abs(strikes - F))
    sigma_0_init = market_vols[atm_idx]
    alpha_init = 0.3
    rho_init = -0.3
    
    result = least_squares(residuals, [alpha_init, rho_init, sigma_0_init],
                          bounds=([0.001, -0.999, 0.001], 
                                 [5.0, 0.999, 5.0]))
    
    return result.x
```

---

## Open-Source Implementations

### 1. pysabr (⭐ 614)
**Repository**: https://github.com/ynouri/pysabr  
**License**: MIT  
**Language**: Python

**Features**:
- SABR model implementation (lognormal and normal)
- SABR calibration to market volatilities
- SABR implied volatility computation
- Jupyter notebooks with examples

**Installation**:
```bash
pip install pysabr
```

**Usage**:
```python
from pysabr import Hagan2002LognormalSabr
from pysabr.models.sabr import SabrModel

# Create SABR model
sabr = SabrModel(f=100, t=0.25, alpha=0.3, beta=0.5, rho=-0.3, vol_vol=0.4)

# Compute implied volatility
sigma = sabr.normal_iv(k=105)

# Calibrate to market data
from pysabr.models.sabr_lognormal import SabrLognormalModel

model = SabrLognormalModel(f=100, t=0.25)
calibrated = model.calibrate(strikes=[90, 95, 100, 105, 110],
                           market_vols=[0.28, 0.24, 0.22, 0.21, 0.23],
                           beta=0.5)
```

### 2. QuantLib (C++ with Python bindings)
**Repository**: https://github.com/lballabio/QuantLib  
**License**: BSD-3-Clause

**Features**:
- SVI interpolation (`SviInterpolation`)
- SABR model with multiple approximations
- Complete volatility surface construction
- SSVI support
- Professional-grade implementation

**Python Usage via SWIG**:
```python
import QuantLib as ql

# Create SVI interpolation
strikes = [ql.QuoteHandle(ql.SimpleQuote(k)) for k in [90, 95, 100, 105, 110]]
vols = [ql.QuoteHandle(ql.SimpleQuote(v)) for v in [0.28, 0.24, 0.22, 0.21, 0.23]]

# SVI parameters: a, b, rho, m, sigma
svi_params = ql.Array([-0.04, 0.1, -0.3, 0.0, 0.3])

# Create interpolation
interp = ql.SviInterpolation(strikes, len(strikes), svi_params)

# Evaluate at any strike
test_strike = 98.0
fitted_vol = interp(test_strike, True)

# SABR model
sabr = ql.SabrVolSurface(
    ql.QuoteHandle(ql.SimpleQuote(100)),  # forward
    ql.Actual365Fixed(),
    [ql.Period(3, ql.Months)],
    [0.25],
    strikes,
    vols,
    0.5  # beta
)
```

### 3. Other Notable Repositories

| Repository | Stars | Description |
|-----------|-------|-------------|
| **wabu-py/options-volatility** | ~50 | Python IV surface fitting |
| **volatility-surface-fitting** | ~30 | SVI fitting examples |
| **jack-gilmoreferris/ssvi** | ~20 | SSVI implementation |
| **option-volatility-pricing** | ~100 | Volatility modeling library |

### 4. QuantLib Python (Recommended for Production)

```bash
pip install QuantLib-Python
```

QuantLib provides the most complete and battle-tested implementation of:
- SVI interpolation
- SABR calibration (multiple approximations)
- Volatility term structures
- Full surface construction with no-arbitrage constraints

---

## Alpaca Options Chain Integration

### Alpaca Options API

Alpaca provides options data through their Market Data API:

**Base URL**: `https://data.alpaca.markets`

**Key Endpoints**:

1. **Get Option Chain**
```
GET /v1beta1/options/snapshots/{underlyingSymbol}
```

Parameters:
- `underlyingSymbol`: Stock ticker (e.g., "SPY", "AAPL)
- `feed`: "indicative" or "us_options" (default)
- `limit`: Number of snapshots (default: 100)

2. **Get Option Contract Details**
```
GET /v1beta1/options/contracts/{symbol}
```

3. **Historical Options Data**
```
GET /v1beta1/options/bars
```

### Fetching Full Options Chain

```python
import requests
import pandas as pd
from datetime import datetime, timedelta

class AlpacaOptionsChain:
    def __init__(self, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = "https://data.alpaca.markets"
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key
        }
    
    def get_option_chain(self, underlying_symbol, expiration_date=None):
        """
        Fetch complete option chain for underlying.
        
        Returns DataFrame with columns:
        - symbol, strike, expiry, type (call/put), bid, ask, 
          last, volume, open_interest, implied_volatility
        """
        url = f"{self.base_url}/v1beta1/options/snapshots/{underlying_symbol}"
        params = {"feed": "us_options", "limit": 1000}
        
        response = requests.get(url, headers=self.headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        
        # Parse snapshots into DataFrame
        rows = []
        for snap in data.get("snapshots", []):
            contract = snap.get("latestTrade", {})
            greeks = snap.get("greeks", {})
            
            # Parse option symbol to extract strike and expiry
            # Format: AAPL250117C00150000
            symbol = snap.get("symbol", "")
            
            rows.append({
                "symbol": symbol,
                "bid": snap.get("latestQuote", {}).get("bp", None),
                "ask": snap.get("latestQuote", {}).get("ap", None),
                "last": contract.get("p", None),
                "volume": snap.get("dailyBar", {}).get("v", 0),
                "open_interest": snap.get("openInterest", None),
                "implied_volatility": greeks.get("implied_volatility", None),
                "delta": greeks.get("delta", None),
                "gamma": greeks.get("gamma", None),
                "theta": greeks.get("theta", None),
                "vega": greeks.get("vega", None),
            })
        
        df = pd.DataFrame(rows)
        
        # Filter by expiration if specified
        if expiration_date:
            df = df[df["expiry"] == expiration_date]
        
        return df
    
    def get_full_chain(self, underlying_symbol):
        """
        Fetch complete chain with all expirations.
        Returns dict of {expiry: DataFrame}
        """
        # Get all contracts
        url = f"{self.base_url}/v1beta1/options/contracts"
        params = {"underlying_symbols": underlying_symbol}
        
        response = requests.get(url, headers=self.headers, params=params)
        response.raise_for_status()
        
        contracts = response.json().get("option_contracts", [])
        
        # Group by expiration
        expiries = {}
        for contract in contracts:
            expiry = contract.get("expiration_date")
            if expiry not in expiries:
                expiries[expiry] = []
            expiries[expiry].append(contract)
        
        return expiries
    
    def compute_iv_from_chain(self, chain_df, forward_price, risk_free_rate=0.05):
        """
        Compute implied volatilities from option prices using Black-Scholes.
        """
        from scipy.stats import norm
        from scipy.optimize import brentq
        
        def bs_price(F, K, T, sigma, option_type='call'):
            d1 = (np.log(F / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
            d2 = d1 - sigma * np.sqrt(T)
            if option_type == 'call':
                return F * norm.cdf(d1) - K * norm.cdf(d2)
            else:
                return K * norm.cdf(-d2) - F * norm.cdf(-d1)
        
        def implied_vol(price, F, K, T, option_type):
            try:
                def objective(sigma):
                    return bs_price(F, K, T, sigma, option_type) - price
                
                return brentq(objective, 1e-6, 5.0)
            except:
                return None
        
        # Compute IV for each option
        chain_df = chain_df.copy()
        chain_df['computed_iv'] = chain_df.apply(
            lambda row: implied_vol(
                (row['bid'] + row['ask']) / 2,  # Use mid-price
                forward_price,
                row['strike'],
                row['days_to_expiry'] / 365,
                row['type']
            ),
            axis=1
        )
        
        return chain_df
```

### Processing Alpaca Data for SVI Fitting

```python
def prepare_svi_data(chain_df, expiry_date, forward_price):
    """
    Prepare option chain data for SVI fitting.
    
    Returns:
        log_moneyness, total_variances, strikes, ivs
    """
    # Filter by expiry and remove illiquid options
    mask = (
        (chain_df['expiry'] == expiry_date) &
        (chain_df['volume'] > 10) &
        (chain_df['open_interest'] > 50) &
        (chain_df['bid'] > 0.01)  # Remove penny options
    )
    
    df = chain_df[mask].copy()
    
    # Compute time to expiry
    T = df['days_to_expiry'].iloc[0] / 365
    
    # Use mid-price for IV calculation
    df['mid_price'] = (df['bid'] + df['ask']) / 2
    
    # Filter out options with zero or negative mid-price
    df = df[df['mid_price'] > 0]
    
    # Compute log-moneyness
    df['log_moneyness'] = np.log(df['strike'] / forward_price)
    
    # Compute total variance
    df['total_variance'] = df['implied_volatility']**2 * T
    
    return (
        df['log_moneyness'].values,
        df['total_variance'].values,
        df['strike'].values,
        df['implied_volatility'].values
    )
```

---

## IV Surface Construction Pipeline

### End-to-End Pipeline

```python
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from dataclasses import dataclass
from typing import List, Dict, Tuple

@dataclass
class IVSurfacePoint:
    """Single point on the IV surface."""
    strike: float
    expiry: float  # years
    iv: float
    log_moneyness: float
    forward: float

class IVSurfaceBuilder:
    """
    Complete IV surface construction from market data.
    """
    
    def __init__(self, spot_price: float, risk_free_rate: float = 0.05):
        self.spot = spot_price
        self.r = risk_free_rate
        self.surface_data = {}  # {expiry: (log_moneyness, total_var, strikes, ivs)}
        self.svi_params = {}    # {expiry: (a, b, rho, m, sigma)}
        self.ssvi_params = None  # Global SSVI parameters
    
    def add_expiry_data(self, expiry: float, strikes: np.ndarray, 
                        ivs: np.ndarray, forward: float):
        """Add data for a single expiry."""
        log_moneyness = np.log(strikes / forward)
        total_variances = ivs**2 * expiry
        
        self.surface_data[expiry] = {
            'log_moneyness': log_moneyness,
            'total_variances': total_variances,
            'strikes': strikes,
            'ivs': ivs,
            'forward': forward
        }
    
    def fit_svi_per_expiry(self) -> Dict[float, Tuple]:
        """Fit SVI to each expiry independently."""
        for expiry, data in self.surface_data.items():
            params = self._fit_single_svi(
                data['log_moneyness'],
                data['total_variances'],
                expiry
            )
            self.svi_params[expiry] = params
        return self.svi_params
    
    def fit_ssvi_surface(self) -> Tuple:
        """Fit SSVI across all expiries (global fit)."""
        # Collect all data points
        all_k, all_T, all_w = [], [], []
        
        for expiry, data in self.surface_data.items():
            all_k.extend(data['log_moneyness'])
            all_T.extend([expiry] * len(data['log_moneyness']))
            all_w.extend(data['total_variances'])
        
        all_k = np.array(all_k)
        all_T = np.array(all_T)
        all_w = np.array(all_w)
        
        # Fit SSVI parameters
        self.ssvi_params = self._fit_ssvi(all_k, all_T, all_w)
        return self.ssvi_params
    
    def get_iv(self, strike: float, expiry: float, method: str = 'svi') -> float:
        """
        Get implied volatility at any (strike, expiry) point.
        
        Methods: 'svi' (per-expiry), 'ssvi' (global), 'linear' (interpolation)
        """
        if method == 'svi' and expiry in self.svi_params:
            params = self.svi_params[expiry]
            forward = self.surface_data[expiry]['forward']
            k = np.log(strike / forward)
            w = self._svi_formula(k, *params)
            return np.sqrt(w / expiry) if w > 0 else 0
        
        elif method == 'ssvi' and self.ssvi_params:
            rho, theta_a, theta_b, phi_c, phi_d = self.ssvi_params
            forward = self._get_forward_for_expiry(expiry)
            k = np.log(strike / forward)
            theta_T = theta_a * expiry + theta_b * expiry**2
            phi_T = phi_c / (1 + phi_d * expiry)
            w = self._ssvi_formula(k, expiry, theta_T, phi_T, rho)
            return np.sqrt(w / expiry) if w > 0 else 0
        
        else:
            # Linear interpolation as fallback
            return self._linear_interpolate_iv(strike, expiry)
    
    def get_surface_grid(self, k_range=(-0.3, 0.3), t_range=None, 
                         n_k=50, n_t=50) -> Tuple:
        """Generate a regular grid of IV values."""
        if t_range is None:
            t_range = (min(self.surface_data.keys()), 
                      max(self.surface_data.keys()))
        
        k_grid = np.linspace(k_range[0], k_range[1], n_k)
        t_grid = np.linspace(t_range[0], t_range[1], n_t)
        
        iv_grid = np.zeros((n_t, n_k))
        
        for i, T in enumerate(t_grid):
            for j, k in enumerate(k_grid):
                # Convert log-moneyness back to strike
                # Use forward from nearest expiry
                nearest_expiry = min(self.surface_data.keys(), 
                                   key=lambda x: abs(x - T))
                forward = self.surface_data[nearest_expiry]['forward']
                strike = forward * np.exp(k)
                
                iv_grid[i, j] = self.get_iv(strike, T, method='svi')
        
        return k_grid, t_grid, iv_grid
    
    def _fit_single_svi(self, log_moneyness, total_variances, expiry):
        """Fit SVI to a single expiry."""
        def objective(params):
            a, b, rho, m, sigma = params
            fitted = self._svi_formula(log_moneyness, a, b, rho, m, sigma)
            return np.sum((fitted - total_variances)**2)
        
        # Initial guess
        w_min = np.min(total_variances)
        k_min = log_moneyness[np.argmin(total_variances)]
        initial = [w_min, 0.1, -0.3, k_min, 0.3]
        
        bounds = [(None, None), (0, None), (-0.999, 0.999), 
                  (None, None), (1e-6, None)]
        
        result = minimize(objective, initial, method='L-BFGS-B', bounds=bounds)
        return result.x
    
    def _svi_formula(self, k, a, b, rho, m, sigma):
        """SVI total variance formula."""
        return a + b * (rho * (k - m) + np.sqrt((k - m)**2 + sigma**2))
    
    def _ssvi_formula(self, k, T, theta_T, phi_T, rho):
        """SSVI total variance formula."""
        k_star = k / np.sqrt(theta_T)
        return theta_T * (1 + phi_T * rho * k_star + 
                         np.sqrt(k_star**2 - 2 * rho * k_star + 1))
    
    def _fit_ssvi(self, all_k, all_T, all_w):
        """Fit global SSVI parameters."""
        def objective(params):
            rho, theta_a, theta_b, phi_c, phi_d = params
            fitted_w = np.array([
                self._ssvi_formula(k, T, 
                                  theta_a * T + theta_b * T**2,
                                  phi_c / (1 + phi_d * T),
                                  rho)
                for k, T in zip(all_k, all_T)
            ])
            return np.sum((fitted_w - all_w)**2)
        
        initial = [-0.3, 0.04, 0.1, 0.5, 1.0]
        bounds = [(-0.99, 0.99), (0, None), (0, None), (0, None), (0, None)]
        
        result = minimize(objective, initial, method='L-BFGS-B', bounds=bounds)
        return result.x
```

---

## Trading Signals from IV Surface

### 1. **Volatility Skew Analysis**

The skew captures the asymmetry in the IV smile. Negative skew (typical for equities) means puts have higher IV than calls.

```python
def compute_skew(surface_builder, expiry, otm_put_strike, otm_call_strike, 
                  forward):
    """
    Compute 25-delta risk reversal as a measure of skew.
    
    Risk Reversal = IV(25Δ call) - IV(25Δ put)
    Negative = Put skew dominant (typical)
    """
    iv_put = surface_builder.get_iv(otm_put_strike, expiry)
    iv_call = surface_builder.get_iv(otm_call_strike, expiry)
    
    return iv_call - iv_put

def skew_signal(skew, historical_mean, historical_std):
    """
    Generate trading signal from skew.
    
    Returns:
        > 0: Skew is unusually flat → potential put selling opportunity
        < 0: Skew is unusually steep → potential call selling / put buying
    """
    z_score = (skew - historical_mean) / historical_std
    
    if z_score > 2:
        return "SELL_PUTS"  # Skew too flat
    elif z_score < -2:
        return "BUY_PUTS"   # Skew too steep
    return "NEUTRAL"
```

### 2. **Term Structure Analysis**

The term structure compares IV across expiries.

```python
def term_structure_slope(surface_builder, short_expiry, long_expiry, 
                         moneyness=0):
    """
    Compute term structure slope (front-month vs back-month).
    
    Returns: IV(long) - IV(short) for ATM options
    """
    # Get ATM strikes
    forward_short = surface_builder.surface_data[short_expiry]['forward']
    forward_long = surface_builder.surface_data[long_expiry]['forward']
    
    iv_short = surface_builder.get_iv(forward_short, short_expiry)
    iv_long = surface_builder.get_iv(forward_long, long_expiry)
    
    return iv_long - iv_short

def term_structure_signal(slope, historical_mean, historical_std):
    """
    Generate signal from term structure slope.
    
    Positive slope: Back-month IV > Front-month (normal)
    Negative slope: Front-month IV > Back-month (backwardation → potential event)
    """
    z_score = (slope - historical_mean) / historical_std
    
    if z_score < -2:
        return "CALENDAR_SPREAD_SELL_FRONT"  # Front month elevated
    elif z_score > 2:
        return "CALENDAR_SPREAD_SELL_BACK"   # Back month elevated
    return "NEUTRAL"
```

### 3. **Smile Curvature Analysis**

```python
def smile_curvature(surface_builder, expiry, forward):
    """
    Compute butterfly spread as a measure of smile curvature.
    
    Butterfly = 0.5 * IV(K-Δ) + 0.5 * IV(K+Δ) - IV(K)
    """
    # 25-delta strikes (approximate)
    delta_strike_otm = forward * 0.9   # 10% OTM put
    delta_strike_itm = forward * 1.1   # 10% OTM call
    
    iv_put = surface_builder.get_iv(delta_strike_otm, expiry)
    iv_atm = surface_builder.get_iv(forward, expiry)
    iv_call = surface_builder.get_iv(delta_strike_itm, expiry)
    
    butterfly = 0.5 * iv_put + 0.5 * iv_call - iv_atm
    
    return butterfly

def curvature_signal(curvature, historical_mean, historical_std):
    """
    Signal from smile curvature.
    
    High curvature: Expect larger moves than ATM vol suggests
    Low curvature: Expect smaller moves
    """
    z_score = (curvature - historical_mean) / historical_std
    
    if z_score > 2:
        return "SELL_STRADDLE"  # Overpriced vol
    elif z_score < -2:
        return "BUY_STRADDLE"   # Underpriced vol
    return "NEUTRAL"
```

### 4. **Surface Slope Arbitrage**

```python
def surface_slope_arbitrage(surface_builder, expiry1, expiry2, 
                            strike1, strike2):
    """
    Detect relative value opportunities across the surface.
    
    Compares IV at different (strike, expiry) combinations.
    """
    iv1 = surface_builder.get_iv(strike1, expiry1)
    iv2 = surface_builder.get_iv(strike2, expiry2)
    
    # Convert to total variance
    w1 = iv1**2 * expiry1
    w2 = iv2**2 * expiry2
    
    # Check for butterfly arbitrage in total variance
    # (simplified check)
    if w1 > w2 and expiry1 < expiry2:
        return "BACKWARDATION_SPIKE"
    elif w1 < w2 and expiry1 > expiry2:
        return "CONTANGO_FLATTENING"
    
    return None
```

### 5. **Volatility Regime Detection**

```python
def vol_regime_signal(surface_builder, expiry):
    """
    Detect volatility regime using ATM IV relative to historical distribution.
    """
    forward = surface_builder.surface_data[expiry]['forward']
    current_iv = surface_builder.get_iv(forward, expiry)
    
    # Compare to historical (would need historical data)
    # Simple percentile-based approach
    
    return {
        'current_iv': current_iv,
        'regime': classify_regime(current_iv),
        'percentile': compute_percentile(current_iv)
    }

def classify_regime(iv, low_threshold=0.15, high_threshold=0.30):
    if iv < low_threshold:
        return "LOW_VOL"
    elif iv > high_threshold:
        return "HIGH_VOL"
    return "NORMAL_VOL"
```

---

## Skew/Smile Term Structure Analysis

### Key Metrics

| Metric | Formula | Meaning |
|--------|---------|---------|
| **25Δ Risk Reversal** | IV(25Δ call) - IV(25Δ put) | Skew measure |
| **25Δ Butterfly** | 0.5*IV(25Δ put) + 0.5*IV(25Δ call) - IV(ATM) | Smile curvature |
| **ATM Vol** | IV(at-the-money) | Level of volatility |
| **Term Structure Slope** | IV(back-month) - IV(front-month) | Time structure |
| **Wing Spread** | IV(10Δ put) - IV(10Δ call) | Extreme tail risk |

### Computing Delta-Adjusted Strikes

```python
from scipy.stats import norm
import numpy as np

def delta_to_strike(forward, delta, T, option_type='put', 
                    sigma=None, r=0.0):
    """
    Convert option delta to strike price.
    
    For a put with delta = -0.25:
        delta = -N(-d1)
        d1 = N_inv(-delta)  [for puts]
    """
    if sigma is None:
        raise ValueError("IV needed for precise delta-strike conversion")
    
    if option_type == 'put':
        # Put delta = -N(-d1) = -e^(-rT) * N(-d1)
        # For simplicity, use |delta| = N(-d1)
        d1 = norm.ppf(-delta)
        # d1 = (ln(F/K) + 0.5*σ²*T) / (σ*sqrt(T))
        # Solve for K:
        ln_K = np.log(forward) - d1 * sigma * np.sqrt(T) - 0.5 * sigma**2 * T
        return np.exp(ln_K)
    else:
        # Call delta = N(d1)
        d1 = norm.ppf(delta)
        ln_K = np.log(forward) + d1 * sigma * np.sqrt(T) - 0.5 * sigma**2 * T
        return np.exp(ln_K)

def get_25d_strikes(forward, T, sigma_atm, r=0.0):
    """Get strikes corresponding to 25-delta options."""
    put_25d = delta_to_strike(forward, -0.25, T, 'put', sigma_atm, r)
    call_25d = delta_to_strike(forward, 0.25, T, 'call', sigma_atm, r)
    return put_25d, call_25d

def get_10d_strikes(forward, T, sigma_atm, r=0.0):
    """Get strikes corresponding to 10-delta options."""
    put_10d = delta_to_strike(forward, -0.10, T, 'put', sigma_atm, r)
    call_10d = delta_to_strike(forward, 0.10, T, 'call', sigma_atm, r)
    return put_10d, call_10d
```

### Term Structure Metrics

```python
def term_structure_metrics(surface_builder):
    """
    Compute comprehensive term structure metrics.
    """
    expiries = sorted(surface_builder.surface_data.keys())
    
    metrics = []
    
    for i, expiry in enumerate(expiries):
        forward = surface_builder.surface_data[expiry]['forward']
        
        # ATM IV
        atm_iv = surface_builder.get_iv(forward, expiry)
        
        # Skew (25Δ risk reversal)
        put_25d, call_25d = get_25d_strikes(forward, expiry, atm_iv)
        iv_put_25d = surface_builder.get_iv(put_25d, expiry)
        iv_call_25d = surface_builder.get_iv(call_25d, expiry)
        risk_reversal = iv_call_25d - iv_put_25d
        
        # Butterfly (25Δ)
        butterfly_25d = 0.5 * iv_put_25d + 0.5 * iv_call_25d - atm_iv
        
        # Term structure slope
        if i > 0:
            prev_expiry = expiries[i-1]
            prev_forward = surface_builder.surface_data[prev_expiry]['forward']
            prev_atm = surface_builder.get_iv(prev_forward, prev_expiry)
            term_slope = atm_iv - prev_atm
        else:
            term_slope = None
        
        metrics.append({
            'expiry': expiry,
            'atm_iv': atm_iv,
            'risk_reversal_25d': risk_reversal,
            'butterfly_25d': butterfly_25d,
            'term_slope': term_slope
        })
    
    return pd.DataFrame(metrics)
```

---

## Institutional Best Practices

### 1. **Data Quality & Filtering**
- Remove illiquid options (low volume/OI)
- Filter out stale quotes
- Handle penny options carefully
- Use bid-ask midpoints for IV calculation
- Exclude options with < 5 days to expiry (pin risk)

### 2. **Arbitrage Constraints**
```python
def check_arbitrage_constraints(strikes, ivs, T):
    """
    Verify no-arbitrage conditions.
    """
    # 1. Call spread arbitrage: C(K1) >= C(K2) for K1 < K2
    # 2. Put spread arbitrage: P(K1) <= P(K2) for K1 < K2
    # 3. Butterfly arbitrage: C(K-δ) - 2C(K) + C(K+δ) >= 0
    
    # In IV terms, check butterfly condition
    for i in range(1, len(strikes) - 1):
        iv_prev = ivs[i-1]
        iv_curr = ivs[i]
        iv_next = ivs[i+1]
        
        # Butterfly in total variance space
        w_prev = iv_prev**2 * T
        w_curr = iv_curr**2 * T
        w_next = iv_next**2 * T
        
        butterfly = 0.5 * w_prev - w_curr + 0.5 * w_next
        
        if butterfly < -1e-6:  # Small tolerance for numerical errors
            return False, f"Butterfly violation at strike {strikes[i]}"
    
    return True, "No arbitrage detected"
```

### 3. **Regularization Techniques**
```python
def regularized_svi_fit(log_moneyness, total_variances, 
                        lambda_smooth=0.01):
    """
    Regularized SVI fitting to prevent overfitting.
    """
    def objective(params):
        a, b, rho, m, sigma = params
        
        # Data fitting term
        fitted = svi_total_variance(log_moneyness, a, b, rho, m, sigma)
        fit_error = np.sum((fitted - total_variances)**2)
        
        # Smoothness penalty (second derivative)
        k_fine = np.linspace(np.min(log_moneyness), 
                            np.max(log_moneyness), 100)
        w_fine = svi_total_variance(k_fine, a, b, rho, m, sigma)
        second_deriv = np.diff(w_fine, n=2)
        smoothness = np.sum(second_deriv**2)
        
        return fit_error + lambda_smooth * smoothness
    
    result = minimize(objective, initial_guess, method='L-BFGS-B')
    return result.x
```

### 4. **Handling Missing Data**
```python
def interpolate_surface(surface_builder, expiry, strikes, method='svi'):
    """
    Interpolate IV at strikes where market data is missing.
    """
    # Use SVI parameters to fill gaps
    return [surface_builder.get_iv(K, expiry, method=method) 
            for K in strikes]
```

### 5. **Real-time Surface Updates**
```python
class RealtimeIVSurface:
    """
    Live IV surface that updates with market data.
    """
    
    def __init__(self, spot, update_interval=60):
        self.builder = IVSurfaceBuilder(spot)
        self.last_update = None
        self.update_interval = update_interval
    
    def on_market_data(self, chain_data):
        """Process incoming market data."""
        # Filter and validate
        clean_data = self._clean_chain_data(chain_data)
        
        # Fit surface
        self.builder.fit_svi_per_expiry()
        
        self.last_update = datetime.now()
    
    def _clean_chain_data(self, data):
        """Apply data quality filters."""
        # Remove low-volume options
        # Remove stale quotes
        # Handle gaps
        return data
```

---

## Key References

### Academic Papers
1. **Gatheral, J. (2004)** - "A parsimonious arbitrage-free implied volatility parameterization with application to the valuation of volatility derivatives"
   - Original SVI parametrization
   - Available at: SSRN 1019050

2. **Gatheral, J. & Jacquier, A. (2014)** - "Arbitrage-free smile interpolations"
   - SSVI (Surface SVI)
   - Provides arbitrage-free surface construction

3. **Hagan, P.S. et al. (2002)** - "Managing Smile Risk"
   - SABR model
   - Industry standard for interest rate smiles

4. **Bergomi, L. (2015)** - "Stochastic Volatility Modeling"
   - Comprehensive treatment of vol surfaces
   - Advanced parametrizations and trading applications

### Open Source Libraries

| Library | Language | Features | Stars |
|---------|----------|----------|-------|
| **pysabr** | Python | SABR model | 614 |
| **QuantLib** | C++/Python | Full derivatives library | 5k+ |
| **py_vollib** | Python | IV, Greeks | 500+ |
| **vollib** | Python | IV calculation | 300+ |

### Data Sources
- **Alpaca Markets**: Free options data API
- **CBOE**: Official options data
- **Ivolatility.com**: Historical IV data
- **OptionMetrics**: Institutional-grade data

---

## Implementation Checklist

- [ ] Set up Alpaca API credentials
- [ ] Implement option chain fetching
- [ ] Convert prices to implied volatilities
- [ ] Fit SVI per expiry
- [ ] Fit SSVI surface (global)
- [ ] Implement SABR calibration
- [ ] Build IV surface builder class
- [ ] Compute skew metrics (risk reversal, butterfly)
- [ ] Implement term structure analysis
- [ ] Add arbitrage constraint checking
- [ ] Build trading signal generators
- [ ] Add real-time surface updates
- [ ] Validate with backtesting
- [ ] Deploy to production trading engine

---

*Document created: 2026-07-03*  
*Last updated: 2026-07-03*
