#!/usr/bin/env python3
"""
COMPLETE OPTIONS GREEKS REFERENCE & TRADING TOOLKIT
====================================================
Institutional-grade Greeks analysis for options trading.

Covers:
1. All Greeks (1st, 2nd, 3rd order) with Black-Scholes formulas
2. Gamma-based position management (gamma exposure, scalping, risk)
3. Theta-based income strategies (decay curves, optimal collection)
4. Vega-based volatility trading (exposure, hedging, plays)
5. Vanna & Charm deep dive (dealer flow analysis)
6. Portfolio Greeks management (net Greeks, budgeting, targets)
7. Greeks visualization dashboard
8. Greeks hedging strategies (delta, gamma, vega)
9. Greeks in different market conditions
"""

import numpy as np
from scipy.stats import norm
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# SECTION 1: COMPLETE GREEKS REFERENCE - EVERY GREEK DEFINED
# ============================================================================

class BlackScholesGreeks:
    """
    Complete Black-Scholes Greeks calculator.
    
    All formulas are for European options with continuous dividend yield.
    
    Notation:
        S = Stock price
        K = Strike price
        r = Risk-free rate (annualized)
        q = Dividend yield (annualized)
        σ = Volatility (annualized)
        τ = Time to expiry (in years)
        T = Expiry time
        t = Current time
        τ = T - t
    """
    
    @staticmethod
    def d1(S, K, r, q, sigma, tau):
        """Calculate d1 parameter"""
        return (np.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
    
    @staticmethod
    def d2(S, K, r, q, sigma, tau):
        """Calculate d2 parameter"""
        return BlackScholesGreeks.d1(S, K, r, q, sigma, tau) - sigma * np.sqrt(tau)
    
    # ---- FIRST-ORDER GREEKS ----
    
    @staticmethod
    def delta(S, K, r, q, sigma, tau, option_type='call'):
        """
        DELTA (Δ) - Rate of change of option price w.r.t. underlying price
        
        Formula:
            Call: Δ = e^{-qτ} Φ(d1)
            Put:  Δ = -e^{-qτ} Φ(-d1)
        
        Interpretation:
            - Measures directional exposure
            - Call delta: 0 to +1.0 (approximates probability of expiring ITM)
            - Put delta: 0 to -1.0
            - ATM options ≈ ±0.50
            - Deep ITM ≈ ±1.0
            - Deep OTM ≈ 0
        
        Trading Applications:
            - Position sizing (1 option with Δ=0.50 behaves like 50 shares)
            - Delta hedging (buy/sell underlying to neutralize)
            - Probability proxy: |Δ| ≈ probability of expiring ITM
            - Directional bets: buy calls (positive Δ) for bullish, buy puts (negative Δ) for bearish
        """
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        if option_type == 'call':
            return np.exp(-q * tau) * norm.cdf(d1)
        else:
            return -np.exp(-q * tau) * norm.cdf(-d1)
    
    @staticmethod
    def gamma(S, K, r, q, sigma, tau):
        """
        GAMMA (Γ) - Rate of change of delta w.r.t. underlying price
        
        Formula:
            Γ = e^{-qτ} φ(d1) / (S σ √τ)
            = K e^{-rτ} φ(d2) / (S² σ √τ)
        
        Note: Gamma is the SAME for calls and puts.
        
        Interpretation:
            - Second derivative of option price w.r.t. S
            - Maximum at-the-money (ATM)
            - Decreases for deep ITM and OTM options
            - Higher for shorter-dated options (near expiry)
            - Positive for long options, negative for short options
        
        Trading Applications:
            - Gamma scalping (delta hedging to profit from gamma)
            - Gamma risk: large moves create non-linear P&L
            - Gamma squeeze: when dealers are short gamma, moves accelerate
            - Position management: monitor gamma to assess convexity risk
        """
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        return np.exp(-q * tau) * norm.pdf(d1) / (S * sigma * np.sqrt(tau))
    
    @staticmethod
    def theta(S, K, r, q, sigma, tau, option_type='call'):
        """
        THETA (Θ) - Rate of change of option price w.r.t. time
        
        Formula:
            Call: Θ = -e^{-qτ} S φ(d1) σ / (2√τ) - rKe^{-rτ} Φ(d2) + qSe^{-qτ} Φ(d1)
            Put:  Θ = -e^{-qτ} S φ(d1) σ / (2√τ) + rKe^{-rτ} Φ(-d2) - qSe^{-qτ} Φ(-d1)
        
        Interpretation:
            - Time decay of option value (typically negative for long options)
            - Accelerates as expiration approaches
            - Maximum decay for ATM options
            - Expressed per calendar day (divide by 365)
            - Weekend/holiday decay is NOT captured by formula
        
        Trading Applications:
            - Theta decay is the "enemy" for option buyers
            - Income strategies: sell options to collect theta (credit spreads, iron condors)
            - Optimal theta collection: sell options 30-45 DTE for best theta/gamma ratio
            - Theta burn: ATM options lose ~1/3 of value in last 30 days
        """
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        
        common = -np.exp(-q * tau) * S * norm.pdf(d1) * sigma / (2 * np.sqrt(tau))
        
        if option_type == 'call':
            return common - r * K * np.exp(-r * tau) * norm.cdf(d2) + q * S * np.exp(-q * tau) * norm.cdf(d1)
        else:
            return common + r * K * np.exp(-r * tau) * norm.cdf(-d2) - q * S * np.exp(-q * tau) * norm.cdf(-d1)
    
    @staticmethod
    def vega(S, K, r, q, sigma, tau):
        """
        VEGA (ν) - Rate of change of option price w.r.t. volatility
        
        Formula:
            ν = Se^{-qτ} φ(d1) √τ = Ke^{-rτ} φ(d2) √τ
        
        Note: Vega is the SAME for calls and puts.
        
        Interpretation:
            - Sensitivity to 1% change in implied volatility
            - Maximum for ATM options
            - Increases with time to expiry (longer-dated = more vega)
            - Positive for both long calls and puts
        
        Trading Applications:
            - Volatility trading: buy options when IV is low (positive vega)
            - Vega hedging: use options at different strikes/expiries
            - Earnings plays: buy straddles before earnings for vega exposure
            - Vol crush: sell options before earnings to capture theta, lose vega
            - Volatility arbitrage: exploit mispricings in IV surface
        """
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        return S * np.exp(-q * tau) * norm.pdf(d1) * np.sqrt(tau)
    
    @staticmethod
    def rho(S, K, r, q, sigma, tau, option_type='call'):
        """
        RHO (ρ) - Rate of change of option price w.r.t. interest rate
        
        Formula:
            Call: ρ = Kτ e^{-rτ} Φ(d2)
            Put:  ρ = -Kτ e^{-rτ} Φ(-d2)
        
        Interpretation:
            - Least used first-order Greek for short-dated options
            - More significant for LEAPS and long-dated options
            - Calls have positive rho (rates up → calls up)
            - Puts have negative rho (rates up → puts down)
        
        Trading Applications:
            - LEAPS portfolio: rho becomes significant for 1+ year options
            - Rate environment: rising rates benefit calls, hurt puts
            - Bond market correlation: rho links options to rates market
        """
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        if option_type == 'call':
            return K * tau * np.exp(-r * tau) * norm.cdf(d2)
        else:
            return -K * tau * np.exp(-r * tau) * norm.cdf(-d2)
    
    @staticmethod
    def lambda_(S, K, r, q, sigma, tau, option_type='call'):
        """
        LAMBDA (λ) / OMEGA (Ω) - Percentage change in option price per % change in S
        
        Formula:
            λ = Ω = Δ × (S / V)
        
        Interpretation:
            - Leverage factor of the option
            - Shows how much the option amplifies stock moves (in % terms)
            - Deep OTM options have very high lambda (lottery tickets)
            - ATM options: λ ≈ 2-4x typically
        """
        delta = BlackScholesGreeks.delta(S, K, r, q, sigma, tau, option_type)
        price = BlackScholesGreeks.price(S, K, r, q, sigma, tau, option_type)
        if price > 0:
            return delta * S / price
        return 0
    
    @staticmethod
    def epsilon(S, K, r, q, sigma, tau, option_type='call'):
        """
        EPSILON (ε) - Sensitivity to dividend yield
        
        Formula:
            Call: ε = -Sτ e^{-qτ} Φ(d1)
            Put:  ε = Sτ e^{-qτ} Φ(-d1)
        """
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        if option_type == 'call':
            return -S * tau * np.exp(-q * tau) * norm.cdf(d1)
        else:
            return S * tau * np.exp(-q * tau) * norm.cdf(-d1)
    
    # ---- SECOND-ORDER GREEKS ----
    
    @staticmethod
    def vanna(S, K, r, q, sigma, tau):
        """
        VANNA - Second-order Greek: ∂Δ/∂σ = ∂ν/∂S
        
        Formula:
            vanna = -e^{-qτ} φ(d1) × d2/σ
            = (ν/S) × [1 - d1/(σ√τ)]
        
        Interpretation:
            - Sensitivity of delta to changes in volatility
            - Sensitivity of vega to changes in underlying price
            - Critical for dealer hedging flow analysis
            - Negative for ATM options (delta decreases when vol increases)
            - Positive for OTM options
        
        Trading Applications:
            - Dealer hedging: dealers who sold options (short vega) need to buy 
              delta when vol falls (positive vanna flow)
            - Volatility regime changes: vanna drives delta changes
            - Risk reversals: vanna explains why risk reversals are priced as they are
            - Vanna-flies: sell ATM straddles, buy OTM wings to exploit vanna
        """
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        return -np.exp(-q * tau) * norm.pdf(d1) * d2 / sigma
    
    @staticmethod
    def charm(S, K, r, q, sigma, tau, option_type='call'):
        """
        CHARM (δ decay / DdeltaDtime) - Rate of change of delta w.r.t. time
        
        Formula:
            Charm = -∂Δ/∂τ = ∂Θ/∂S = -∂²V/(∂τ∂S)
        
            Call: charm = qe^{-qτ}Φ(d1) - e^{-qτ}φ(d1) × [2(r-q)τ - d2σ√τ]/(2τσ√τ)
            Put:  charm = -qe^{-qτ}Φ(-d1) - e^{-qτ}φ(d1) × [2(r-q)τ - d2σ√τ]/(2τσ√τ)
        
        Interpretation:
            - How quickly delta decays per day
            - Critical for weekend/overnight delta management
            - ITM call delta approaches 1 as time passes (charm positive)
            - ITM put delta approaches -1 as time passes (charm negative)
            - OTM options: delta decays toward 0
        
        Trading Applications:
            - Weekend hedging: monitor charm to predict Monday opening delta
            - Day-trading: charm drives intraday delta changes
            - Earnings: charm is less relevant than vanna before events
            - Hedging effectiveness: charm degrades hedges over time
        """
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        
        common = -np.exp(-q * tau) * norm.pdf(d1) * (2 * (r - q) * tau - d2 * sigma * np.sqrt(tau)) / (2 * tau * sigma * np.sqrt(tau))
        
        if option_type == 'call':
            return q * np.exp(-q * tau) * norm.cdf(d1) + common
        else:
            return -q * np.exp(-q * tau) * norm.cdf(-d1) + common
    
    @staticmethod
    def vomma(S, K, r, q, sigma, tau):
        """
        VOMMA / VOLGA / VEGA CONVEXITY (DvegaDvol) - Second-order vega sensitivity
        
        Formula:
            vomma = ∂²V/∂σ² = ν × d1 × d2 / σ
        
        Interpretation:
            - Rate of change of vega as volatility changes
            - Positive for OTM options (long vol convexity)
            - Can be "scalped" analogous to gamma scalping
        
        Trading Applications:
            - Long vomma positions benefit from vol moves in either direction
            - Butterfly spreads have positive vomma
            - Volatility smile trading
        """
        vega = BlackScholesGreeks.vega(S, K, r, q, sigma, tau)
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        return vega * d1 * d2 / sigma
    
    @staticmethod
    def veta(S, K, r, q, sigma, tau):
        """
        VETA / VEGA DECAY (DvegaDtime) - Rate of change of vega w.r.t. time
        
        Formula:
            veta = -∂ν/∂τ = ∂²V/(∂σ∂τ)
        
        Interpretation:
            - How quickly vega decays over time
            - Longer-dated options have higher veta
            - Important for managing volatility exposure over time
        """
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        vega = BlackScholesGreeks.vega(S, K, r, q, sigma, tau)
        return -vega * (q + (r - q) * d1 / (sigma * np.sqrt(tau)) - (1 + d1 * d2) / (2 * tau))
    
    @staticmethod
    def vera(S, K, r, q, sigma, tau):
        """
        VERA (Rhova) - Rate of change of rho w.r.t. volatility
        
        Formula:
            vera = ∂ρ/∂σ = ∂ν/∂r
        """
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        return -K * tau * np.exp(-r * tau) * norm.pdf(d2) * np.sqrt(tau)
    
    # ---- THIRD-ORDER GREEKS ----
    
    @staticmethod
    def speed(S, K, r, q, sigma, tau):
        """
        SPEED - Rate of change of gamma w.r.t. underlying price (DgammaDspot)
        
        Formula:
            Speed = ∂Γ/∂S = ∂³V/∂S³ = -Γ/S × (d1/(σ√τ) + 1)
        
        Interpretation:
            - How quickly gamma changes with price moves
            - Important for gamma-hedged portfolios
            - Negative for ATM options (gamma decreases as S moves away from K)
        """
        gamma = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau)
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        return -gamma / S * (d1 / (sigma * np.sqrt(tau)) + 1)
    
    @staticmethod
    def zomma(S, K, r, q, sigma, tau):
        """
        ZOMMA - Rate of change of gamma w.r.t. volatility (DgammaDvol)
        
        Formula:
            zomma = ∂Γ/∂σ = ∂vanna/∂S = ∂³V/(∂S²∂σ)
        
        Interpretation:
            - Important for gamma-hedged portfolios when vol changes
            - Helps anticipate hedge effectiveness during vol regime changes
        """
        gamma = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau)
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        return gamma * (d1 * d2 - 1) / sigma
    
    @staticmethod
    def color(S, K, r, q, sigma, tau):
        """
        COLOR (Gamma Decay / DgammaDtime) - Rate of change of gamma w.r.t. time
        
        Formula:
            color = ∂Γ/∂τ = ∂³V/(∂S²∂τ)
        
        Interpretation:
            - How quickly gamma decays over time
            - Important for gamma-hedged portfolios
            - Color changes rapidly near expiration
        """
        gamma = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau)
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        tau_sqrt = np.sqrt(tau)
        return -gamma * (2*(r-q)*tau - d2*sigma*tau_sqrt) / (2*tau*tau_sqrt) - gamma * d1*d2 / (2*tau)
    
    # ---- OPTION PRICING ----
    
    @staticmethod
    def price(S, K, r, q, sigma, tau, option_type='call'):
        """Black-Scholes option price"""
        d1 = BlackScholesGreeks.d1(S, K, r, q, sigma, tau)
        d2 = BlackScholesGreeks.d2(S, K, r, q, sigma, tau)
        
        if option_type == 'call':
            return S * np.exp(-q * tau) * norm.cdf(d1) - K * np.exp(-r * tau) * norm.cdf(d2)
        else:
            return K * np.exp(-r * tau) * norm.cdf(-d2) - S * np.exp(-q * tau) * norm.cdf(-d1)


# ============================================================================
# SECTION 2: GAMMA-BASED POSITION MANAGEMENT
# ============================================================================

class GammaAnalysis:
    """
    Gamma-based position management: exposure, scalping, risk.
    """
    
    @staticmethod
    def gamma_exposure(GX: float, S: float, notional: float = None) -> dict:
        """
        Calculate Gamma Exposure (GEX) for a portfolio.
        
        GEX measures the dollar exposure to a 1% move in the underlying.
        
        GEX = Γ × S² × 0.01 × (number of contracts × 100)
        
        Interpretation:
            - Positive GEX: Portfolio benefits from large moves (long gamma)
            - Negative GEX: Portfolio suffers from large moves (short gamma)
            - Dealers typically short gamma (negative GEX) → they hedge by selling
              into rallies and buying into dips → dampening moves
            - When GEX flips positive, moves accelerate (gamma squeeze)
        """
        if notional is None:
            notional = 1.0  # per contract
        gex_per_contract = GX * S * S * 0.01
        total_gex = gex_per_contract * notional
        
        return {
            'gamma_per_share': GX,
            'gex_per_contract': gex_per_contract,
            'total_gex': total_gex,
            'interpretation': 'Long Gamma (move amplifier)' if total_gex > 0 else 'Short Gamma (move dampener)'
        }
    
    @staticmethod
    def gamma_scalping_pnl(gamma: float, S: float, sigma: float, dt: float,
                          contracts: int = 1) -> dict:
        """
        Gamma scalping P&L estimation.
        
        Strategy: Buy options (long gamma), delta-hedge continuously.
        
        The daily expected P&L from gamma scalping:
            E[P&L] = 0.5 × Γ × S² × σ² × dt - Θ × dt
        
        Where:
            0.5 × Γ × S² × σ² × dt = gamma P&L from realized moves
            Θ × dt = theta paid to maintain the position
        
        Break-even: Realized vol > Implied vol (the option was "cheap")
        
        Key Insight:
            - Long gamma profits from realized volatility > implied volatility
            - Short gamma profits from realized volatility < implied volatility
            - Gamma scalping is essentially a volatility trade
        """
        # Expected gamma P&L per day (from realized moves)
        gamma_pl = 0.5 * gamma * S**2 * sigma**2 * dt
        
        # Daily theta cost
        theta = BlackScholesGreeks.theta(S, S, 0.05, 0, sigma, 30/365)  # example ATM
        theta_cost = abs(theta) * dt
        
        net_daily = (gamma_pl - theta_cost) * contracts * 100
        
        return {
            'gamma_pnl_per_day': gamma_pl * contracts * 100,
            'theta_cost_per_day': theta_cost * contracts * 100,
            'net_pnl_per_day': net_daily,
            'annual_gamma_pnl': gamma_pl * contracts * 100 * 252,
            'annual_theta_cost': theta_cost * contracts * 100 * 252,
            'breakeven_realized_vol': BlackScholesGreeks.vega(S, S, 0.05, 0, 0.30, 30/365)  # implied vol at which gamma=theta
        }
    
    @staticmethod
    def gamma_risk_analysis(portfolio: List[dict]) -> dict:
        """
        Analyze gamma risk across a portfolio.
        
        Portfolio Gamma = Σ(position_gamma × contracts × 100)
        
        Risk metrics:
            - Net gamma: directional convexity exposure
            - Gamma per $1M notional: normalized gamma exposure
            - Gamma concentration: where is the gamma risk concentrated?
        """
        total_gamma = 0
        total_notional = 0
        positions_gamma = []
        
        for pos in portfolio:
            S = pos['S']
            K = pos['K']
            tau = pos['tau']
            sigma = pos['sigma']
            contracts = pos['contracts']
            option_type = pos.get('type', 'call')
            
            gamma = BlackScholesGreeks.gamma(S, K, 0.05, 0, sigma, tau)
            pos_gamma = gamma * contracts * 100
            notional = S * contracts * 100
            
            total_gamma += pos_gamma
            total_notional += notional
            positions_gamma.append({
                'strike': K,
                'tau': tau,
                'gamma': gamma,
                'position_gamma': pos_gamma,
                'notional': notional,
                'type': option_type
            })
        
        return {
            'total_gamma': total_gamma,
            'total_notional': total_notional,
            'gamma_per_million': total_gamma / (total_notional / 1_000_000) if total_notional > 0 else 0,
            'positions': sorted(positions_gamma, key=lambda x: abs(x['position_gamma']), reverse=True),
            'risk_level': 'HIGH' if abs(total_gamma) > 1000 else 'MEDIUM' if abs(total_gamma) > 100 else 'LOW'
        }


# ============================================================================
# SECTION 3: THETA-BASED INCOME STRATEGIES
# ============================================================================

class ThetaAnalysis:
    """
    Theta-based income strategies: decay curves, optimal collection.
    """
    
    @staticmethod
    def theta_decay_curve(S, K, r, q, sigma, option_type='call'):
        """
        Generate theta decay curve over time.
        
        Theta Decay Characteristics:
            - Theta accelerates as expiry approaches
            - ATM options decay fastest in absolute terms
            - OTM options decay fastest in percentage terms
            - Last 30 days: ~1/3 of total decay
            - Last 7 days: ~1/2 of remaining decay
        
        Optimal Theta Collection:
            - Sell options 30-45 DTE (best theta/gamma ratio)
            - Close at 50% profit or 21 DTE (whichever comes first)
            - Weekly options: higher theta but more gamma risk
            - Monthly options: more stable theta, less gamma risk
        """
        taus = np.linspace(90/365, 1/365, 90)  # 90 days to 1 day
        thetas = []
        prices = []
        
        for tau in taus:
            if tau > 0:
                theta = BlackScholesGreeks.theta(S, K, r, q, sigma, tau, option_type)
                price = BlackScholesGreeks.price(S, K, r, q, sigma, tau, option_type)
                thetas.append(theta / 365)  # daily theta
                prices.append(price)
            else:
                thetas.append(0)
                prices.append(0)
        
        return {
            'days_to_expiry': list(range(90, 0, -1)),
            'daily_theta': thetas,
            'option_price': prices,
            'cumulative_decay': np.cumsum(thetas),
            'decay_acceleration': np.diff(thetas) if len(thetas) > 1 else []
        }
    
    @staticmethod
    def optimal_theta_strategy(S, sigma, risk_free=0.05):
        """
        Find the optimal DTE for theta collection.
        
        The theta/gamma ratio (Sharpe-like metric for theta sellers):
            θ/Γ ratio = |Θ| / Γ
        
        Higher ratio = more theta per unit of gamma risk
        
        Optimal range: 30-45 DTE where theta/gamma ratio peaks.
        """
        results = []
        for dte in range(7, 120):
            tau = dte / 365
            # ATM option
            theta = BlackScholesGreeks.theta(S, S, risk_free, 0, sigma, tau)
            gamma = BlackScholesGreeks.gamma(S, S, risk_free, 0, sigma, tau)
            vega = BlackScholesGreeks.vega(S, S, risk_free, 0, sigma, tau)
            
            if gamma > 0:
                theta_gamma_ratio = abs(theta) / gamma
            else:
                theta_gamma_ratio = 0
            
            results.append({
                'dte': dte,
                'daily_theta': abs(theta) / 365,
                'gamma': gamma,
                'theta_gamma_ratio': theta_gamma_ratio,
                'vega': vega
            })
        
        # Find optimal DTE
        optimal = max(results, key=lambda x: x['theta_gamma_ratio'])
        
        return {
            'optimal_dte': optimal['dte'],
            'optimal_theta_gamma_ratio': optimal['theta_gamma_ratio'],
            'optimal_daily_theta': optimal['daily_theta'],
            'all_dtes': results,
            'recommendation': f"Optimal selling window: {optimal['dte']} DTE with θ/Γ ratio of {optimal['theta_gamma_ratio']:.2f}"
        }
    
    @staticmethod
    def income_strategy_comparison(S, sigma, risk_free=0.05):
        """
        Compare different income strategies by their Greeks profile.
        
        Strategies compared:
            1. Short Strangle (sell OTM call + put)
            2. Iron Condor (limited risk version of strangle)
            3. Credit Spread (single directional)
            4. Covered Call (stock + short call)
            5. Cash-Secured Put (short put)
        """
        strategies = []
        
        # 1. Short Strangle (10 delta wings, 30 DTE)
        call_strike = S * 1.15  # OTM call
        put_strike = S * 0.85   # OTM put
        tau = 30 / 365
        
        call_gamma = BlackScholesGreeks.gamma(S, call_strike, risk_free, 0, sigma, tau)
        put_gamma = BlackScholesGreeks.gamma(S, put_strike, risk_free, 0, sigma, tau)
        call_theta = BlackScholesGreeks.theta(S, call_strike, risk_free, 0, sigma, tau, 'call')
        put_theta = BlackScholesGreeks.theta(S, put_strike, risk_free, 0, sigma, tau, 'put')
        call_vega = BlackScholesGreeks.vega(S, call_strike, risk_free, 0, sigma, tau)
        put_vega = BlackScholesGreeks.vega(S, put_strike, risk_free, 0, sigma, tau)
        
        strategies.append({
            'name': 'Short Strangle',
            'net_delta': BlackScholesGreeks.delta(S, call_strike, risk_free, 0, sigma, tau, 'call') + 
                        BlackScholesGreeks.delta(S, put_strike, risk_free, 0, sigma, tau, 'put'),
            'net_gamma': -(call_gamma + put_gamma),
            'net_theta': -(call_theta + put_theta),
            'net_vega': -(call_vega + put_vega),
            'max_profit': 'Premium collected',
            'max_loss': 'Undefined',
            'best_market': 'Range-bound, declining vol'
        })
        
        # 2. Iron Condor (25 delta wings, 30 DTE)
        ic_call_outer = S * 1.20
        ic_call_inner = S * 1.10
        ic_put_outer = S * 0.80
        ic_put_inner = S * 0.90
        
        strategies.append({
            'name': 'Iron Condor',
            'net_delta': (BlackScholesGreeks.delta(S, ic_call_outer, risk_free, 0, sigma, tau, 'call') +
                         BlackScholesGreeks.delta(S, ic_call_inner, risk_free, 0, sigma, tau, 'put') +
                         BlackScholesGreeks.delta(S, ic_put_outer, risk_free, 0, sigma, tau, 'put') +
                         BlackScholesGreeks.delta(S, ic_put_inner, risk_free, 0, sigma, tau, 'call')),
            'net_gamma': -(BlackScholesGreeks.gamma(S, ic_call_outer, risk_free, 0, sigma, tau) +
                          BlackScholesGreeks.gamma(S, ic_call_inner, risk_free, 0, sigma, tau) +
                          BlackScholesGreeks.gamma(S, ic_put_outer, risk_free, 0, sigma, tau) +
                          BlackScholesGreeks.gamma(S, ic_put_inner, risk_free, 0, sigma, tau)),
            'net_theta': -(BlackScholesGreeks.theta(S, ic_call_outer, risk_free, 0, sigma, tau, 'call') +
                          BlackScholesGreeks.theta(S, ic_call_inner, risk_free, 0, sigma, tau, 'put') +
                          BlackScholesGreeks.theta(S, ic_put_outer, risk_free, 0, sigma, tau, 'put') +
                          BlackScholesGreeks.theta(S, ic_put_inner, risk_free, 0, sigma, tau, 'call')),
            'net_vega': -(BlackScholesGreeks.vega(S, ic_call_outer, risk_free, 0, sigma, tau) +
                         BlackScholesGreeks.vega(S, ic_call_inner, risk_free, 0, sigma, tau) +
                         BlackScholesGreeks.vega(S, ic_put_outer, risk_free, 0, sigma, tau) +
                         BlackScholesGreeks.vega(S, ic_put_inner, risk_free, 0, sigma, tau)),
            'max_profit': 'Net premium received',
            'max_loss': 'Width of wider spread - premium',
            'best_market': 'Range-bound, declining vol, defined risk'
        })
        
        return strategies


# ============================================================================
# SECTION 4: VEGA-BASED VOLATILITY TRADING
# ============================================================================

class VegaAnalysis:
    """
    Vega-based volatility trading: exposure, hedging, plays.
    """
    
    @staticmethod
    def vega_exposure_analysis(S, K, r, q, sigma, tau, contracts=1):
        """
        Calculate vega exposure for volatility trading.
        
        Vega = ∂V/∂σ
        
        Key relationships:
            - Vega ∝ √τ (vega increases with time)
            - Vega peaks ATM
            - Vega for calls ≈ Vega for puts (same strike)
            - Vega decreases for deep OTM/ITM options
        
        Volatility trading framework:
            1. Long vega: Buy straddles/strangles when IV is low
            2. Short vega: Sell straddles/strangles when IV is high
            3. Vega-neutral: Use calendar spreads or ratio spreads
        """
        vega = BlackScholesGreeks.vega(S, K, r, q, sigma, tau)
        total_vega = vega * contracts * 100
        
        # Vega as % of option price
        price = BlackScholesGreeks.price(S, K, r, q, sigma, tau, 'call')
        vega_pct = (vega * 0.01 / price * 100) if price > 0 else 0  # impact of 1 vol point
        
        return {
            'vega_per_contract': vega,
            'total_vega': total_vega,
            'price_impact_1vol': vega * 0.01 * contracts * 100,  # P&L for 1% vol move
            'vega_as_pct_of_price': vega_pct,
            'vol_breakeven': sigma,  # implied vol at which position breaks even
            'interpretation': {
                'long_vega': f"Position gains ${total_vega:.2f} for each 1% increase in IV",
                'vol_needed_to_profit': f"If IV rises {vega_pct:.1f}%, option gains 1% of its value"
            }
        }
    
    @staticmethod
    def volatility_surface_analysis(S, strikes, taus, r=0.05, q=0.0):
        """
        Analyze the implied volatility surface.
        
        The volatility surface shows how IV varies across strikes and expiries:
            - Volatility smile: IV higher for OTM puts and calls (vs ATM)
            - Volatility skew: IV higher for OTM puts (protective put demand)
            - Term structure: IV varies with time to expiry
        
        Surface characteristics:
            - Front months: steeper smile (more gamma risk)
            - Back months: flatter smile (more vega risk)
            - During crashes: skew steepens dramatically
        """
        surface = {}
        for tau in taus:
            surface[tau] = {}
            for K in strikes:
                # Add realistic skew: OTM puts have higher IV
                moneyness = np.log(S / K)
                base_vol = 0.25  # base ATM vol
                skew = 0.10 * moneyness**2  # smile component
                skew_tilt = -0.05 * moneyness   # put skew
                term = 0.02 * (tau - 30/365)   # term structure
                
                iv = base_vol + skew + skew_tilt + term
                surface[tau][K] = max(iv, 0.05)  # floor at 5%
        
        return surface
    
    @staticmethod
    def vega_hedging_strategy(S, sigma, r=0.05, q=0.0):
        """
        Vega hedging strategies using options at different strikes/expiries.
        
        Goal: Maintain desired vega exposure while hedging unwanted risks.
        
        Strategy 1: Calendar Spread (Vega hedge via different expiries)
            - Buy front-month, sell back-month
            - Net vega depends on ratio
            - Captures term structure changes
        
        Strategy 2: Ratio Spread (Vega hedge via different strikes)
            - Buy ATM options, sell OTM options
            - Adjust ratio to target net vega
        
        Strategy 3: VIX/Variance Swaps (Direct vol exposure)
            - VIX futures for direct vol exposure
            - Variance swaps for quadratic vol exposure
        """
        # Calendar spread: buy 30D, sell 60D ATM straddle
        front_tau = 30 / 365
        back_tau = 60 / 365
        
        front_vega = BlackScholesGreeks.vega(S, S, r, q, sigma, front_tau)
        back_vega = BlackScholesGreeks.vega(S, S, r, q, sigma, back_tau)
        
        # Find ratio to make vega-neutral
        if back_vega > 0:
            hedge_ratio = front_vega / back_vega
        else:
            hedge_ratio = 1.0
        
        # Butterfly: buy 2 ATM, sell 1 OTM call, sell 1 OTM put
        butterfly_call = S * 1.10
        butterfly_put = S * 0.90
        butterfly_vega = 2 * BlackScholesGreeks.vega(S, S, r, q, sigma, front_tau) - \
                        BlackScholesGreeks.vega(S, butterfly_call, r, q, sigma, front_tau) - \
                        BlackScholesGreeks.vega(S, butterfly_put, r, q, sigma, front_tau)
        
        return {
            'calendar_spread': {
                'front_vega': front_vega,
                'back_vega': back_vega,
                'hedge_ratio': hedge_ratio,
                'description': f"Buy {1:.1f} front-month, sell {hedge_ratio:.2f} back-month for vega-neutral"
            },
            'butterfly': {
                'butterfly_vega': butterfly_vega,
                'description': f"Butterfly vega: {butterfly_vega:.4f} per butterfly"
            },
            'straddle_comparison': {
                '30d_vega': front_vega,
                '60d_vega': back_vega,
                '90d_vega': BlackScholesGreeks.vega(S, S, r, q, sigma, 90/365),
                'note': 'Vega increases with time to expiry'
            }
        }
    
    @staticmethod
    def earnings_volatility_play(S, sigma, earnings_iv_premium=0.10, r=0.05, q=0.0):
        """
        Earnings volatility play analysis.
        
        Pre-earnings:
            - IV rises in anticipation (vega positive)
            - Buy straddle/strangle to be long vega
        
        Post-earnings:
            - IV crashes (vol crush)
            - Theta accelerates (time decay)
        
        Strategy comparison:
            1. Long straddle: Profit if move > expected (long vega, long gamma)
            2. Short straddle: Profit if move < expected (short vega, short gamma)
            3. Iron condor: Defined risk short vol (short vega, short gamma)
            4. Calendar spread: Term structure play (mixed vega)
        """
        tau_pre = 10 / 365   # 10 days before earnings
        tau_post = 5 / 365   # 5 days after earnings (1 week post)
        
        # Pre-earnings pricing (elevated IV)
        pre_vol = sigma + earnings_iv_premium
        pre_price = BlackScholesGreeks.price(S, S, r, q, pre_vol, tau_pre, 'call')
        pre_vega = BlackScholesGreeks.vega(S, S, r, q, pre_vol, tau_pre)
        
        # Post-earnings pricing (vol crush + time decay)
        post_vol = sigma  # back to normal
        post_price = BlackScholesGreeks.price(S, S, r, q, post_vol, tau_post, 'call')
        
        # Straddle comparison
        pre_straddle = 2 * pre_price  # approximate ATM straddle
        post_straddle = 2 * post_price
        
        return {
            'pre_earnings': {
                'iv': pre_vol,
                'straddle_price': pre_straddle,
                'vega': pre_vega,
                'theta': BlackScholesGreeks.theta(S, S, r, q, pre_vol, tau_pre) * 2
            },
            'post_earnings': {
                'iv': post_vol,
                'straddle_price': post_straddle,
                'vol_crush': earnings_iv_premium,
                'theta_benefit': 'Time decay accelerates post-earnings'
            },
            'expected_move': S * pre_vol * np.sqrt(tau_pre),
            'strategy_recommendation': {
                'long_straddle': f"Buy straddle at ${pre_straddle:.2f}, profit if move > ${S * pre_vol * np.sqrt(tau_pre):.2f}",
                'short_straddle': f"Sell straddle at ${pre_straddle:.2f}, profit if move < ${S * pre_vol * np.sqrt(tau_pre):.2f}",
                'calendar_spread': "Buy back-month, sell front-month: benefits from vol crush while maintaining vega"
            }
        }


# ============================================================================
# SECTION 5: VANNA & CHARM DEEP DIVE (DEALER FLOW)
# ============================================================================

class VannaCharmAnalysis:
    """
    Vanna and Charm deep dive - how dealers use these for flow analysis.
    """
    
    @staticmethod
    def dealer_hedging_flow(S, strikes, taus, r=0.05, q=0.0, sigma=0.25):
        """
        Analyze dealer hedging flow based on vanna and charm.
        
        Dealer Position:
            - Dealers typically sell options to clients (short vega, short gamma)
            - They hedge by buying/selling the underlying
            - Their hedging creates predictable flow patterns
        
        Vanna Flow:
            - When IV falls: delta increases for short OTM puts → dealers buy
            - When IV rises: delta decreases for short OTM puts → dealers sell
            - This is the "vanna flow" that drives markets
        
        Charm Flow:
            - As time passes: OTM put delta decays toward 0 → dealers buy back
            - As time passes: OTM call delta decays toward 0 → dealers sell
            - This creates end-of-day/week flow patterns
        
        Combined Effect:
            - Vanna flow dominates during vol regime changes
            - Charm flow dominates during normal time decay
            - Near expiry: charm flow accelerates (pin risk)
        """
        flow_analysis = {}
        
        for tau in taus:
            daily_flow = {'total_delta_change': 0, 'positions': []}
            
            for K in strikes:
                # Vanna: change in delta per 1% vol change
                vanna = BlackScholesGreeks.vanna(S, K, r, q, sigma, tau)
                
                # Charm: change in delta per day
                charm_call = BlackScholesGreeks.charm(S, K, r, q, sigma, tau, 'call')
                charm_put = BlackScholesGreeks.charm(S, K, r, q, sigma, tau, 'put')
                
                # For a dealer who SOLD this option:
                # Short call: vanna flow = -vanna (if vol falls, delta rises → must sell stock)
                # Short put: vanna flow = +vanna (if vol falls, delta becomes less negative → must buy stock)
                
                dealer_flow = {
                    'strike': K,
                    'dte': int(tau * 365),
                    'vanna_per_1vol': -vanna,  # negative = short vega position
                    'charm_per_day_call': -charm_call,
                    'charm_per_day_put': -charm_put,
                    'vanna_hedge_needed': -vanna * 0.01 * S * 100,  # shares to buy/sell per 1% vol drop
                    'charm_hedge_needed': -charm_call / 365 * S * 100  # shares to buy/sell per day
                }
                
                daily_flow['positions'].append(dealer_flow)
                daily_flow['total_delta_change'] += dealer_flow['charm_hedge_needed']
            
            flow_analysis[int(tau * 365)] = daily_flow
        
        return flow_analysis
    
    @staticmethod
    def vanna_flower_trade(S, sigma, r=0.05, q=0.0):
        """
        The Vanna Flower: A specific trade exploiting vanna flow.
        
        Trade structure:
            - Short ATM straddle (negative gamma, negative vega, positive theta)
            - Long OTM puts and calls (positive gamma, positive vega)
            - Net: vega-neutral, gamma-neutral, but positive vanna
        
        Why it works:
            - Dealers are short vega, so they need to buy delta when vol falls
            - The vanna flower profits from this predictable flow
            - Works best in range-bound markets with declining vol
        
        Risk:
            - Sharp moves (gamma risk)
            - Vol spikes (vega risk if not perfectly hedged)
        """
        tau = 30 / 365
        
        # ATM straddle
        atm_gamma = BlackScholesGreeks.gamma(S, S, r, q, sigma, tau)
        atm_vega = BlackScholesGreeks.vega(S, S, r, q, sigma, tau)
        atm_theta = BlackScholesGreeks.theta(S, S, r, q, sigma, tau)
        
        # OTM wings (10% OTM)
        wing_strike_call = S * 1.10
        wing_strike_put = S * 0.90
        wing_gamma = BlackScholesGreeks.gamma(S, wing_strike_call, r, q, sigma, tau)
        wing_vega = BlackScholesGreeks.vega(S, wing_strike_call, r, q, sigma, tau)
        
        # Find wing ratio for vega neutrality
        wing_ratio = atm_vega / wing_vega if wing_vega > 0 else 1
        
        net_gamma = -atm_gamma + wing_ratio * wing_gamma * 2  # call + put wing
        net_vega = -atm_vega + wing_ratio * wing_vega * 2
        net_theta = -atm_theta  # theta from short straddle
        
        return {
            'structure': {
                'short_straddle': f"Short 1 ATM straddle at strike {S}",
                'long_wings': f"Long {wing_ratio:.2f} {wing_strike_call:.0f}C + {wing_ratio:.2f} {wing_strike_put:.0f}P"
            },
            'greeks': {
                'net_delta': 0,  # approximately zero
                'net_gamma': net_gamma,
                'net_vega': net_vega,
                'net_theta': net_theta,
                'net_vanna': BlackScholesGreeks.vanna(S, S, r, q, sigma, tau)
            },
            'expected_pnl': {
                'if_vol_falls_5pct': net_vega * 0.05 + net_theta * 30 / 365,
                'if_vol_rises_5pct': net_vega * 0.05 + net_theta * 30 / 365,
                'if_stock_moves_5pct': 0.5 * net_gamma * S**2 * 0.05**2 + net_theta * 30 / 365
            },
            'optimal_conditions': 'Range-bound market with declining implied volatility'
        }


# ============================================================================
# SECTION 6: PORTFOLIO GREEKS MANAGEMENT
# ============================================================================

class PortfolioGreeks:
    """
    Greeks-based portfolio management: net Greeks, budgeting, targets.
    """
    
    def __init__(self):
        self.positions = []
    
    def add_position(self, S, K, tau, sigma, contracts, option_type='call', 
                     direction='long', description=''):
        """Add an option position to the portfolio."""
        r, q = 0.05, 0.0
        
        if direction == 'short':
            sign = -1
        else:
            sign = 1
        
        delta = BlackScholesGreeks.delta(S, K, r, q, sigma, tau, option_type) * sign * contracts * 100
        gamma = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau) * sign * contracts * 100
        theta = BlackScholesGreeks.theta(S, K, r, q, sigma, tau, option_type) * sign * contracts * 100
        vega = BlackScholesGreeks.vega(S, K, r, q, sigma, tau) * sign * contracts * 100
        rho = BlackScholesGreeks.rho(S, K, r, q, sigma, tau, option_type) * sign * contracts * 100
        price = BlackScholesGreeks.price(S, K, r, q, sigma, tau, option_type)
        
        self.positions.append({
            'description': description or f"{direction} {option_type} K={K} tau={tau*365:.0f}d",
            'S': S, 'K': K, 'tau': tau, 'sigma': sigma,
            'contracts': contracts, 'type': option_type, 'direction': direction,
            'price': price,
            'notional': S * contracts * 100,
            'greeks': {
                'delta': delta,
                'gamma': gamma,
                'theta': theta,
                'vega': vega,
                'rho': rho
            }
        })
    
    def get_net_greeks(self):
        """Calculate net portfolio Greeks."""
        net = {'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0, 'rho': 0}
        total_notional = 0
        
        for pos in self.positions:
            for greek in net:
                net[greek] += pos['greeks'][greek]
            total_notional += pos['notional']
        
        # Normalize per $1M notional
        per_million = {}
        if total_notional > 0:
            for greek in net:
                per_million[greek] = net[greek] / (total_notional / 1_000_000)
        
        return {
            'net_greeks': net,
            'per_million_notional': per_million,
            'total_notional': total_notional,
            'num_positions': len(self.positions),
            'risk_assessment': self._assess_risk(net, total_notional)
        }
    
    def _assess_risk(self, net, notional):
        """Assess portfolio risk based on net Greeks."""
        risks = []
        
        # Delta risk
        delta_pct = abs(net['delta']) / (notional / 100) * 100 if notional > 0 else 0
        if delta_pct > 5:
            risks.append(f"⚠️ High delta exposure: {delta_pct:.1f}% of notional")
        
        # Gamma risk
        gamma_daily = abs(net['gamma']) * 0.01 * (notional / (notional / 100))  # approximate
        if abs(net['gamma']) > 100:
            risks.append(f"⚠️ High gamma exposure: {net['gamma']:.0f} gamma")
        
        # Theta risk
        if net['theta'] < -100:
            risks.append(f"⚠️ Negative theta bleed: ${net['theta']:.0f}/day")
        
        # Vega risk
        if abs(net['vega']) > 500:
            risks.append(f"⚠️ High vega exposure: ${net['vega']:.0f} per 1% vol move")
        
        if not risks:
            risks.append("✅ Portfolio Greeks within acceptable ranges")
        
        return risks
    
    def greeks_budget(self, target_delta=0, target_gamma=0, target_vega=0):
        """
        Greeks budgeting - set targets and show gaps.
        
        Target Greeks should be based on:
            1. Market outlook (directional vs neutral)
            2. Risk tolerance
            3. Income objectives
            4. Volatility outlook
        """
        net = self.get_net_greeks()['net_greeks']
        
        budget = {
            'targets': {
                'delta': target_delta,
                'gamma': target_gamma,
                'vega': target_vega
            },
            'current': net,
            'gaps': {
                'delta_gap': target_delta - net['delta'],
                'gamma_gap': target_gamma - net['gamma'],
                'vega_gap': target_vega - net['vega']
            },
            'adjustments_needed': []
        }
        
        # Suggest adjustments
        if abs(budget['gaps']['delta_gap']) > 10:
            budget['adjustments_needed'].append(
                f"Need {'buy' if budget['gaps']['delta_gap'] > 0 else 'sell'} "
                f"{abs(budget['gaps']['delta_gap']):.0f} shares to delta-hedge"
            )
        
        if abs(budget['gaps']['gamma_gap']) > 50:
            budget['adjustments_needed'].append(
                f"Consider {'buying' if budget['gaps']['gamma_gap'] > 0 else 'selling'} "
                f"ATM options to adjust gamma"
            )
        
        if abs(budget['gaps']['vega_gap']) > 200:
            budget['adjustments_needed'].append(
                f"Consider {'buying' if budget['gaps']['vega_gap'] > 0 else 'selling'} "
                f"longer-dated options to adjust vega"
            )
        
        return budget


# ============================================================================
# SECTION 7: GREEKS VISUALIZATION DASHBOARD
# ============================================================================

class GreeksDashboard:
    """
    Greeks visualization - how to build a Greeks dashboard.
    
    In production, use matplotlib/plotly for visualization.
    This class provides the data structures and calculations.
    """
    
    @staticmethod
    def generate_greeks_surface(S, sigma, r=0.05, q=0.0, 
                                 price_range=(0.7, 1.3), time_range=(1/365, 90/365)):
        """
        Generate Greeks surface data for 3D visualization.
        
        Creates a grid of (price, time) → Greeks values.
        """
        prices = np.linspace(S * price_range[0], S * price_range[1], 50)
        times = np.linspace(time_range[0], time_range[1], 50)
        
        delta_surface = np.zeros((len(times), len(prices)))
        gamma_surface = np.zeros((len(times), len(prices)))
        theta_surface = np.zeros((len(times), len(prices)))
        vega_surface = np.zeros((len(times), len(prices)))
        
        for i, tau in enumerate(times):
            for j, K in enumerate(prices):
                delta_surface[i, j] = BlackScholesGreeks.delta(S, K, r, q, sigma, tau, 'call')
                gamma_surface[i, j] = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau)
                theta_surface[i, j] = BlackScholesGreeks.theta(S, K, r, q, sigma, tau, 'call')
                vega_surface[i, j] = BlackScholesGreeks.vega(S, K, r, q, sigma, tau)
        
        return {
            'prices': prices.tolist(),
            'times': (times * 365).tolist(),  # in days
            'delta': delta_surface.tolist(),
            'gamma': gamma_surface.tolist(),
            'theta': (theta_surface / 365).tolist(),  # daily theta
            'vega': vega_surface.tolist()
        }
    
    @staticmethod
    def generate_theta_decay_visualization(S, sigma, r=0.05, q=0.0):
        """
        Generate theta decay curves for visualization.
        """
        moneyness_levels = [0.80, 0.90, 1.00, 1.10, 1.20]
        dtes = list(range(90, 0, -1))
        
        curves = {}
        for m in moneyness_levels:
            K = S * m
            thetas = []
            prices = []
            for dte in dtes:
                tau = dte / 365
                theta = BlackScholesGreeks.theta(S, K, r, q, sigma, tau, 'call')
                price = BlackScholesGreeks.price(S, K, r, q, sigma, tau, 'call')
                thetas.append(theta / 365)  # daily
                prices.append(price)
            
            curves[f'{m:.0%} Moneyness'] = {
                'dtes': dtes,
                'daily_theta': thetas,
                'prices': prices,
                'initial_price': prices[0] if prices else 0,
                'final_price': prices[-1] if prices else 0
            }
        
        return curves
    
    @staticmethod  
    def generate_gamma_heatmap(S, sigma, r=0.05, q=0.0):
        """
        Generate gamma heatmap for different strikes and expiries.
        """
        strikes = np.linspace(S * 0.80, S * 1.20, 20)
        expiries = [7, 14, 21, 30, 45, 60, 90]
        
        heatmap = np.zeros((len(expiries), len(strikes)))
        
        for i, dte in enumerate(expiries):
            tau = dte / 365
            for j, K in enumerate(strikes):
                heatmap[i, j] = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau)
        
        return {
            'strikes': strikes.tolist(),
            'expiries': expiries,
            'gamma': heatmap.tolist()
        }


# ============================================================================
# SECTION 8: OPTIONS PRICING MODELS COMPARISON
# ============================================================================

class OptionsPricingModels:
    """
    Greeks from different pricing models: Black-Scholes vs Binomial vs Monte Carlo.
    """
    
    @staticmethod
    def binomial_model(S, K, r, q, sigma, tau, N=100, option_type='call'):
        """
        Binomial Option Pricing Model with Greeks extraction.
        
        Advantages over Black-Scholes:
            - Handles American options (early exercise)
            - Handles discrete dividends
            - More intuitive for path-dependent options
            - Greeks can be extracted from the tree
        
        Greeks extraction:
            - Delta: (V_up - V_down) / (S_up - S_down)
            - Gamma: (Delta_up - Delta_down) / (S_up - S_down)
            - Theta: (V_left - V_right) / (2 × dt)
        """
        dt = tau / N
        u = np.exp(sigma * np.sqrt(dt))
        d = 1 / u
        p = (np.exp((r - q) * dt) - d) / (u - d)
        discount = np.exp(-r * dt)
        
        # Build price tree
        prices = np.zeros((N + 1, N + 1))
        for i in range(N + 1):
            for j in range(i + 1):
                prices[j, i] = S * (u ** (i - j)) * (d ** j)
        
        # Build option value tree
        values = np.zeros((N + 1, N + 1))
        
        # Terminal values
        for j in range(N + 1):
            if option_type == 'call':
                values[j, N] = max(prices[j, N] - K, 0)
            else:
                values[j, N] = max(K - prices[j, N], 0)
        
        # Backward induction
        for i in range(N - 1, -1, -1):
            for j in range(i + 1):
                hold_value = discount * (p * values[j, i + 1] + (1 - p) * values[j + 1, i + 1])
                
                # American option early exercise check
                if option_type == 'call':
                    exercise_value = max(prices[j, i] - K, 0)
                else:
                    exercise_value = max(K - prices[j, i], 0)
                
                values[j, i] = max(hold_value, exercise_value)
        
        # Extract Greeks at time 0
        option_price = values[0, 0]
        
        # Delta
        delta = (values[0, 1] - values[1, 1]) / (prices[0, 1] - prices[1, 1])
        
        # Gamma (using three points)
        if N >= 2:
            delta_up = (values[0, 2] - values[1, 2]) / (prices[0, 2] - prices[1, 2])
            delta_down = (values[1, 2] - values[2, 2]) / (prices[1, 2] - prices[2, 2])
            gamma = (delta_up - delta_down) / (0.5 * (prices[0, 2] - prices[2, 2]))
            
            # Theta
            theta = (values[1, 2] - option_price) / (2 * dt)
        else:
            gamma = 0
            theta = 0
        
        return {
            'price': option_price,
            'delta': delta,
            'gamma': gamma,
            'theta': theta,
            'model': 'Binomial',
            'steps': N,
            'note': 'Handles American options and discrete dividends'
        }
    
    @staticmethod
    def monte_carlo_greeks(S, K, r, q, sigma, tau, option_type='call', 
                           n_paths=100000, n_steps=252):
        """
        Monte Carlo option pricing with Greeks via pathwise/Bump-and-Revalue.
        
        Methods for Greeks estimation:
            1. Bump-and-Revalue: Shift parameter, reprice, calculate difference
            2. Pathwise (IPA): Differentiate through the simulation
            3. Likelihood Ratio: Weight paths by derivative of distribution
        
        Advantages:
            - Handles complex path-dependent options
            - No restrictions on distribution
            - Can price exotic options with barriers, Asians, etc.
        
        Disadvantages:
            - Computationally expensive
            - Greeks estimates have simulation error
            - No closed-form solution
        """
        dt = tau / n_steps
        
        # Generate paths
        np.random.seed(42)
        Z = np.random.standard_normal((n_paths, n_steps))
        
        # Geometric Brownian Motion paths
        drift = (r - q - 0.5 * sigma**2) * dt
        diffusion = sigma * np.sqrt(dt)
        
        log_returns = drift + diffusion * Z
        log_prices = np.cumsum(log_returns, axis=1)
        
        # Add initial price
        prices = S * np.exp(np.column_stack([np.zeros(n_paths), log_prices]))
        
        # Terminal payoffs
        if option_type == 'call':
            payoffs = np.maximum(prices[:, -1] - K, 0)
        else:
            payoffs = np.maximum(K - prices[:, -1], 0)
        
        # Option price (discounted expected payoff)
        price = np.exp(-r * tau) * np.mean(payoffs)
        
        # Greeks via Bump-and-Revalue
        # Delta: bump S up by small amount
        bump = S * 0.01  # 1% bump
        prices_up = (S + bump) / S * prices
        if option_type == 'call':
            payoffs_up = np.maximum(prices_up[:, -1] - K, 0)
        else:
            payoffs_up = np.maximum(K - prices_up[:, -1], 0)
        price_up = np.exp(-r * tau) * np.mean(payoffs_up)
        delta = (price_up - price) / bump
        
        # Gamma: bump S down by small amount too
        prices_down = (S - bump) / S * prices
        if option_type == 'call':
            payoffs_down = np.maximum(prices_down[:, -1] - K, 0)
        else:
            payoffs_down = np.maximum(K - prices_down[:, -1], 0)
        price_down = np.exp(-r * tau) * np.mean(payoffs_down)
        gamma = (price_up - 2 * price + price_down) / (bump ** 2)
        
        # Vega: bump sigma
        vol_bump = 0.01  # 1 vol point
        # Recalculate with bumped vol
        drift_up = (r - q - 0.5 * (sigma + vol_bump)**2) * dt
        diffusion_up = (sigma + vol_bump) * np.sqrt(dt)
        log_returns_up = drift_up + diffusion_up * Z
        log_prices_up = np.cumsum(log_returns_up, axis=1)
        prices_vol_up = S * np.exp(np.column_stack([np.zeros(n_paths), log_prices_up]))
        
        if option_type == 'call':
            payoffs_vol_up = np.maximum(prices_vol_up[:, -1] - K, 0)
        else:
            payoffs_vol_up = np.maximum(K - prices_vol_up[:, -1], 0)
        price_vol_up = np.exp(-r * tau) * np.mean(payoffs_vol_up)
        vega = (price_vol_up - price) / vol_bump
        
        # Theta: bump time
        tau_bump = tau - 1/365  # 1 day less
        if tau_bump > 0:
            drift_theta = (r - q - 0.5 * sigma**2) * (tau_bump / n_steps)
            diffusion_theta = sigma * np.sqrt(tau_bump / n_steps)
            log_returns_theta = drift_theta + diffusion_theta * Z[:, :int(tau_bump * n_steps)]
            log_prices_theta = np.cumsum(log_returns_theta, axis=1)
            prices_theta = S * np.exp(np.column_stack([np.zeros(n_paths), log_prices_theta]))
            
            if option_type == 'call':
                payoffs_theta = np.maximum(prices_theta[:, -1] - K, 0)
            else:
                payoffs_theta = np.maximum(K - prices_theta[:, -1], 0)
            price_theta = np.exp(-r * tau_bump) * np.mean(payoffs_theta)
            theta = (price_theta - price)  # per day
        else:
            theta = 0
        
        return {
            'price': price,
            'delta': delta,
            'gamma': gamma,
            'vega': vega,
            'theta': theta,
            'model': 'Monte Carlo',
            'n_paths': n_paths,
            'std_error': np.std(payoffs) / np.sqrt(n_paths) * np.exp(-r * tau),
            'note': 'Handles path-dependent and exotic options'
        }
    
    @staticmethod
    def model_comparison(S, K, r, q, sigma, tau, option_type='call'):
        """
        Compare Greeks across pricing models.
        """
        # Black-Scholes (exact)
        bs_price = BlackScholesGreeks.price(S, K, r, q, sigma, tau, option_type)
        bs_delta = BlackScholesGreeks.delta(S, K, r, q, sigma, tau, option_type)
        bs_gamma = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau)
        bs_theta = BlackScholesGreeks.theta(S, K, r, q, sigma, tau, option_type)
        bs_vega = BlackScholesGreeks.vega(S, K, r, q, sigma, tau)
        
        # Binomial
        bin_result = OptionsPricingModels.binomial_model(S, K, r, q, sigma, tau, N=500, option_type=option_type)
        
        # Monte Carlo
        mc_result = OptionsPricingModels.monte_carlo_greeks(S, K, r, q, sigma, tau, option_type, n_paths=50000)
        
        return {
            'black_scholes': {
                'price': bs_price, 'delta': bs_delta, 'gamma': bs_gamma,
                'theta': bs_theta, 'vega': bs_vega
            },
            'binomial': {
                'price': bin_result['price'], 'delta': bin_result['delta'],
                'gamma': bin_result['gamma'], 'theta': bin_result['theta']
            },
            'monte_carlo': {
                'price': mc_result['price'], 'delta': mc_result['delta'],
                'gamma': mc_result['gamma'], 'theta': mc_result['theta'],
                'vega': mc_result['vega']
            },
            'comparison_notes': {
                'BS': 'Exact, fast, European options only, assumes log-normal distribution',
                'Binomial': 'Approximate, handles American options, discrete dividends',
                'MC': 'Approximate, handles path-dependent options, computationally expensive'
            }
        }


# ============================================================================
# SECTION 9: GREEKS HEDGING STRATEGIES
# ============================================================================

class GreeksHedging:
    """
    Greeks hedging strategies: delta, gamma, vega hedging.
    """
    
    @staticmethod
    def delta_hedging(S, K, r, q, sigma, tau, option_type='call', contracts=10):
        """
        Delta Hedging: Maintain delta-neutral portfolio.
        
        Method:
            1. Calculate portfolio delta
            2. Buy/sell underlying shares to bring delta to zero
            3. Rebalance periodically (daily or on large moves)
        
        Delta Hedge Ratio:
            - Number of shares = -Portfolio Delta / Delta per share
            - For short options: sell shares to hedge
            - For long options: buy shares to hedge
        
        Rebalancing frequency:
            - Too frequent: high transaction costs
            - Too infrequent: hedging error
            - Optimal: daily or when delta exceeds threshold
        
        P&L of delta hedging:
            - If realized vol > implied vol: long gamma profits
            - If realized vol < implied vol: short gamma profits
            - Transaction costs reduce profits
        """
        delta = BlackScholesGreeks.delta(S, K, r, q, sigma, tau, option_type)
        gamma = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau)
        
        position_delta = delta * contracts * 100
        shares_to_hedge = -position_delta
        
        return {
            'option_delta': delta,
            'position_delta': position_delta,
            'shares_to_hedge': shares_to_hedge,
            'hedge_cost': shares_to_hedge * S,
            'hedging_details': {
                'if_call': f"Short {abs(shares_to_hedge):.0f} shares to delta-hedge" if shares_to_hedge < 0 else f"Long {shares_to_hedge:.0f} shares to delta-hedge",
                'rebalance_threshold': f"Rebalance when delta drifts by >{abs(gamma * S * 0.01 * contracts * 100):.0f} shares"
            },
            'hedging_error_sources': [
                'Gamma risk: large moves create non-linear delta changes',
                'Discrete rebalancing: cannot hedge continuously',
                'Transaction costs: frequent rebalancing is expensive',
                'Volatility changes: gamma changes with vol'
            ]
        }
    
    @staticmethod
    def gamma_hedging(S, K, r, q, sigma, tau, option_type='call', contracts=10):
        """
        Gamma Hedging: Neutralize gamma exposure.
        
        Method:
            1. Calculate portfolio gamma
            2. Add options with different gamma to bring net gamma to zero
            3. Maintain delta neutrality simultaneously
        
        Gamma Hedge Ratio:
            - Number of hedge options = -Portfolio Gamma / Gamma per hedge option
            - Use options at different strikes/expiries
            - Must re-hedge delta after adding gamma hedge
        
        When to gamma hedge:
            - Large directional positions
            - Near expiration (gamma increases dramatically)
            - Before earnings/events (large moves expected)
        """
        gamma = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau)
        delta = BlackScholesGreeks.delta(S, K, r, q, sigma, tau, option_type)
        theta = BlackScholesGreeks.theta(S, K, r, q, sigma, tau, option_type)
        
        position_gamma = gamma * contracts * 100
        
        # Find hedge option (ATM, different expiry)
        hedge_tau = 60 / 365  # 60-day option as hedge
        hedge_gamma = BlackScholesGreeks.gamma(S, S, r, q, sigma, hedge_tau)
        hedge_delta = BlackScholesGreeks.delta(S, S, r, q, sigma, hedge_tau, 'call')
        
        hedge_contracts = -position_gamma / (hedge_gamma * 100) if hedge_gamma > 0 else 0
        
        return {
            'original_gamma': gamma,
            'position_gamma': position_gamma,
            'hedge_gamma_per_contract': hedge_gamma,
            'hedge_contracts': hedge_contracts,
            'net_gamma': position_gamma + hedge_contracts * hedge_gamma * 100,
            'hedge_delta_impact': hedge_contracts * hedge_delta * 100,
            'cost_of_gamma_hedge': abs(hedge_contracts) * BlackScholesGreeks.price(S, S, r, q, sigma, hedge_tau, 'call') * 100,
            'strategy': 'Gamma scalping: maintain gamma-neutral, profit from realized vs implied vol'
        }
    
    @staticmethod
    def vega_hedging(S, K, r, q, sigma, tau, option_type='call', contracts=10):
        """
        Vega Hedging: Neutralize volatility exposure.
        
        Method:
            1. Calculate portfolio vega
            2. Add options with different vega to bring net vega to zero
            3. Use options at different strikes or expiries
        
        Vega Hedge Ratio:
            - Number of hedge options = -Portfolio Vega / Vega per hedge option
            - Different strikes: smile/skew exposure
            - Different expiries: term structure exposure
        
        When to vega hedge:
            - Large vol exposure positions
            - Before earnings/events
            - When IV is near extremes
        """
        vega = BlackScholesGreeks.vega(S, K, r, q, sigma, tau)
        position_vega = vega * contracts * 100
        
        # Hedge with different expiry
        hedge_tau = 90 / 365
        hedge_vega = BlackScholesGreeks.vega(S, S, r, q, sigma, hedge_tau)
        hedge_contracts = -position_vega / (hedge_vega * 100) if hedge_vega > 0 else 0
        
        # Also show strike hedge
        hedge_strike_vega = BlackScholesGreeks.vega(S, S * 1.10, r, q, sigma, tau)
        hedge_strike_contracts = -position_vega / (hedge_strike_vega * 100) if hedge_strike_vega > 0 else 0
        
        return {
            'original_vega': vega,
            'position_vega': position_vega,
            'expiry_hedge': {
                'hedge_tau': f"{hedge_tau*365:.0f} days",
                'hedge_vega': hedge_vega,
                'hedge_contracts': hedge_contracts,
                'net_vega_after_hedge': position_vega + hedge_contracts * hedge_vega * 100
            },
            'strike_hedge': {
                'hedge_strike': f"{S * 1.10:.0f}",
                'hedge_vega': hedge_strike_vega,
                'hedge_contracts': hedge_strike_contracts,
                'net_vega_after_hedge': position_vega + hedge_strike_contracts * hedge_strike_vega * 100
            },
            'strategy_notes': [
                'Calendar spreads: hedge vega via different expiries',
                'Ratio spreads: hedge vega via different strikes',
                'VIX options: direct vol hedge (imperfect correlation)',
                'Variance swaps: quadratic vol exposure'
            ]
        }


# ============================================================================
# SECTION 10: GREEKS IN DIFFERENT MARKET CONDITIONS
# ============================================================================

class GreeksMarketConditions:
    """
    Greeks behavior in different market conditions.
    """
    
    @staticmethod
    def greeks_by_volatility_regime(S, K, r=0.05, q=0.0):
        """
        Compare Greeks across volatility regimes.
        
        HIGH VOLATILITY (VIX > 25):
            - Gamma: Lower for ATM (options already have high value)
            - Theta: Higher absolute decay (more extrinsic value to decay)
            - Vega: Higher (more sensitivity to vol changes)
            - Delta: ITM options have higher delta (more certainty)
            - Skew: Steepens (protective puts become expensive)
        
        LOW VOLATILITY (VIX < 15):
            - Gamma: Higher for ATM (options are cheap, convexity is high)
            - Theta: Lower absolute decay (less extrinsic value)
            - Vega: Lower (less sensitivity to vol changes)
            - Delta: OTM options have lower delta (less likely to be ITM)
            - Skew: Flattens (protective puts become cheap)
        """
        regimes = {
            'low_vol': {'sigma': 0.15, 'label': 'Low Vol (VIX ~15)'},
            'normal_vol': {'sigma': 0.25, 'label': 'Normal Vol (VIX ~25)'},
            'high_vol': {'sigma': 0.40, 'label': 'High Vol (VIX ~40)'},
            'extreme_vol': {'sigma': 0.60, 'label': 'Extreme Vol (VIX ~60)'}
        }
        
        tau = 30 / 365  # 30 DTE
        results = {}
        
        for regime, params in regimes.items():
            sigma = params['sigma']
            delta = BlackScholesGreeks.delta(S, K, r, q, sigma, tau, 'call')
            gamma = BlackScholesGreeks.gamma(S, K, r, q, sigma, tau)
            theta = BlackScholesGreeks.theta(S, K, r, q, sigma, tau, 'call')
            vega = BlackScholesGreeks.vega(S, K, r, q, sigma, tau)
            price = BlackScholesGreeks.price(S, K, r, q, sigma, tau, 'call')
            
            results[regime] = {
                'label': params['label'],
                'sigma': sigma,
                'price': price,
                'delta': delta,
                'gamma': gamma,
                'theta': theta / 365,  # daily
                'vega': vega,
                'theta_gamma_ratio': abs(theta / gamma) if gamma > 0 else 0
            }
        
        return results
    
    @staticmethod
    def greeks_by_moneyness(S, r=0.05, q=0.0, sigma=0.25):
        """
        Compare Greeks across moneyness levels.
        
        DEEP ITM (Moneyness > 1.2):
            - Delta ≈ 1.0 (behaves like stock)
            - Gamma ≈ 0 (no convexity)
            - Theta ≈ -rK (carry cost only)
            - Vega ≈ 0 (no vol sensitivity)
        
        ATM (Moneyness ≈ 1.0):
            - Delta ≈ 0.50
            - Gamma: Maximum
            - Theta: Maximum negative
            - Vega: Maximum
        
        DEEP OTM (Moneyness < 0.8):
            - Delta ≈ 0 (lottery ticket)
            - Gamma ≈ 0
            - Theta ≈ 0
            - Vega ≈ 0
        """
        tau = 30 / 365
        moneyness_levels = np.arange(0.70, 1.35, 0.05)
        
        results = {}
        for m in moneyness_levels:
            K = S * m
            results[f'{m:.0%}'] = {
                'moneyness': m,
                'strike': K,
                'delta': BlackScholesGreeks.delta(S, K, r, q, sigma, tau, 'call'),
                'gamma': BlackScholesGreeks.gamma(S, K, r, q, sigma, tau),
                'theta': BlackScholesGreeks.theta(S, K, r, q, sigma, tau, 'call') / 365,
                'vega': BlackScholesGreeks.vega(S, K, r, q, sigma, tau),
                'price': BlackScholesGreeks.price(S, K, r, q, sigma, tau, 'call')
            }
        
        return results
    
    @staticmethod
    def greeks_by_time_to_expiry(S, K, r=0.05, q=0.0, sigma=0.25):
        """
        Compare Greeks across time to expiry.
        
        LONG-DATED (90+ DTE):
            - Delta: Closer to probability estimate
            - Gamma: Low (convexity spread over longer time)
            - Theta: Low daily decay (but high total decay)
            - Vega: High (longer exposure to vol changes)
        
        SHORT-DATED (0-7 DTE):
            - Delta: More binary (either 0 or 1)
            - Gamma: Very high near ATM (pin risk)
            - Theta: Very high daily decay (acceleration)
            - Vega: Low (almost no time for vol to matter)
        """
        dtes = [1, 7, 14, 21, 30, 45, 60, 90, 120, 180, 365]
        
        results = {}
        for dte in dtes:
            tau = dte / 365
            results[f'{dte}D'] = {
                'dte': dte,
                'tau': tau,
                'delta': BlackScholesGreeks.delta(S, K, r, q, sigma, tau, 'call'),
                'gamma': BlackScholesGreeks.gamma(S, K, r, q, sigma, tau),
                'theta': BlackScholesGreeks.theta(S, K, r, q, sigma, tau, 'call') / 365,
                'vega': BlackScholesGreeks.vega(S, K, r, q, sigma, tau),
                'price': BlackScholesGreeks.price(S, K, r, q, sigma, tau, 'call')
            }
        
        return results
    
    @staticmethod
    def trending_vs_rangebound_greeks(S, sigma, r=0.05, q=0.0):
        """
        Greeks behavior in trending vs range-bound markets.
        
        TRENDING MARKET (high realized vol, directional moves):
            - Long gamma benefits: large moves create delta profits
            - Theta cost is worth paying for convexity
            - Delta hedging is costly (frequent rebalancing)
            - Short gamma suffers: adverse moves create losses
        
        RANGE-BOUND MARKET (low realized vol, mean-reverting):
            - Short gamma benefits: time decay wins
            - Theta collection is efficient
            - Delta hedging is easy (small moves)
            - Long gamma suffers: paying theta for no movement
        """
        tau = 30 / 365
        gamma = BlackScholesGreeks.gamma(S, S, r, q, sigma, tau)
        theta = BlackScholesGreeks.theta(S, S, r, q, sigma, tau, 'call')
        
        # Scenarios
        scenarios = {
            'trending_high_vol': {
                'realized_vol': sigma * 1.5,
                'expected_move': S * sigma * 1.5 * np.sqrt(tau),
                'long_gamma_pnl': 0.5 * gamma * S**2 * (sigma * 1.5)**2 * tau + theta * tau,
                'short_gamma_pnl': -(0.5 * gamma * S**2 * (sigma * 1.5)**2 * tau + theta * tau),
                'best_strategy': 'Long gamma (buy straddles)',
                'hedge_frequency': 'Frequent (daily or more)'
            },
            'rangebound_low_vol': {
                'realized_vol': sigma * 0.5,
                'expected_move': S * sigma * 0.5 * np.sqrt(tau),
                'long_gamma_pnl': 0.5 * gamma * S**2 * (sigma * 0.5)**2 * tau + theta * tau,
                'short_gamma_pnl': -(0.5 * gamma * S**2 * (sigma * 0.5)**2 * tau + theta * tau),
                'best_strategy': 'Short gamma (sell straddles/condors)',
                'hedge_frequency': 'Infrequent (weekly)'
            }
        }
        
        return {
            'current_gamma': gamma,
            'current_theta': theta,
            'scenarios': scenarios,
            'decision_framework': {
                'if_realized_gt_implied': 'Buy gamma (straddles, strangles)',
                'if_realized_lt_implied': 'Sell gamma (iron condors, credit spreads)',
                'if_directional': 'Use directional deltas',
                'if_rangebound': 'Use theta collection strategies'
            }
        }


# ============================================================================
# MAIN: DEMONSTRATE ALL CAPABILITIES
# ============================================================================

def main():
    """Demonstrate all Greeks analysis capabilities."""
    
    print("=" * 80)
    print("COMPLETE OPTIONS GREEKS REFERENCE & TRADING TOOLKIT")
    print("=" * 80)
    
    # Example parameters
    S = 100      # Stock price
    K = 100      # Strike price (ATM)
    r = 0.05     # Risk-free rate
    q = 0.02     # Dividend yield
    sigma = 0.25 # Implied volatility
    tau = 30/365 # 30 days to expiry
    
    # ---- SECTION 1: All Greeks ----
    print("\n" + "=" * 60)
    print("1. COMPLETE GREEKS REFERENCE")
    print("=" * 60)
    
    BS = BlackScholesGreeks
    
    print(f"\nStock: ${S}, Strike: ${K}, σ: {sigma:.0%}, τ: {tau*365:.0f} days")
    print(f"Option Price (Call): ${BS.price(S, K, r, q, sigma, tau, 'call'):.4f}")
    
    print("\n--- First-Order Greeks ---")
    print(f"  Delta (Δ):    {BS.delta(S, K, r, q, sigma, tau, 'call'):.6f}")
    print(f"  Vega (ν):     {BS.vega(S, K, r, q, sigma, tau):.6f}")
    print(f"  Theta (Θ):    {BS.theta(S, K, r, q, sigma, tau, 'call')/365:.6f} (per day)")
    print(f"  Rho (ρ):      {BS.rho(S, K, r, q, sigma, tau, 'call'):.6f}")
    print(f"  Lambda (λ):   {BS.lambda_(S, K, r, q, sigma, tau, 'call'):.6f}")
    print(f"  Epsilon (ε):  {BS.epsilon(S, K, r, q, sigma, tau, 'call'):.6f}")
    
    print("\n--- Second-Order Greeks ---")
    print(f"  Gamma (Γ):    {BS.gamma(S, K, r, q, sigma, tau):.6f}")
    print(f"  Vanna:        {BS.vanna(S, K, r, q, sigma, tau):.6f}")
    print(f"  Charm:        {BS.charm(S, K, r, q, sigma, tau, 'call'):.6f}")
    print(f"  Vomma:        {BS.vomma(S, K, r, q, sigma, tau):.6f}")
    print(f"  Veta:         {BS.veta(S, K, r, q, sigma, tau):.6f}")
    print(f"  Vera:         {BS.vera(S, K, r, q, sigma, tau):.6f}")
    
    print("\n--- Third-Order Greeks ---")
    print(f"  Speed:        {BS.speed(S, K, r, q, sigma, tau):.6f}")
    print(f"  Zomma:        {BS.zomma(S, K, r, q, sigma, tau):.6f}")
    print(f"  Color:        {BS.color(S, K, r, q, sigma, tau):.6f}")
    
    # ---- SECTION 2: Gamma Analysis ----
    print("\n" + "=" * 60)
    print("2. GAMMA-BASED POSITION MANAGEMENT")
    print("=" * 60)
    
    gamma_analyzer = GammaAnalysis()
    
    gamma_val = BS.gamma(S, K, r, q, sigma, tau)
    gex = gamma_analyzer.gamma_exposure(gamma_val, S, notional=10)
    print(f"\nGamma Exposure (GEX) for 10 ATM contracts:")
    print(f"  GEX per contract: ${gex['gex_per_contract']:,.2f}")
    print(f"  Total GEX: ${gex['total_gex']:,.2f}")
    print(f"  {gex['interpretation']}")
    
    scalping = gamma_analyzer.gamma_scalping_pnl(gamma_val, S, sigma, 1/365, contracts=1)
    print(f"\nGamma Scalping P&L (daily):")
    print(f"  Gamma P&L: ${scalping['gamma_pnl_per_day']:.2f}")
    print(f"  Theta cost: -${scalping['theta_cost_per_day']:.2f}")
    print(f"  Net daily: ${scalping['net_pnl_per_day']:.2f}")
    
    # ---- SECTION 3: Theta Analysis ----
    print("\n" + "=" * 60)
    print("3. THETA-BASED INCOME STRATEGIES")
    print("=" * 60)
    
    theta_analyzer = ThetaAnalysis()
    
    optimal = theta_analyzer.optimal_theta_strategy(S, sigma, r)
    print(f"\nOptimal Theta Collection:")
    print(f"  {optimal['recommendation']}")
    print(f"  Daily theta at optimal DTE: ${optimal['optimal_daily_theta']:.4f}")
    
    # ---- SECTION 4: Vega Analysis ----
    print("\n" + "=" * 60)
    print("4. VEGA-BASED VOLATILITY TRADING")
    print("=" * 60)
    
    vega_analyzer = VegaAnalysis()
    
    vega_exp = vega_analyzer.vega_exposure_analysis(S, K, r, q, sigma, tau, contracts=10)
    print(f"\nVega Exposure (10 contracts):")
    print(f"  Total vega: ${vega_exp['total_vega']:.2f}")
    print(f"  P&L for 1% vol move: ${vega_exp['price_impact_1vol']:.2f}")
    
    earnings = vega_analyzer.earnings_volatility_play(S, sigma)
    print(f"\nEarnings Volatility Play:")
    print(f"  Expected move: ${earnings['expected_move']:.2f}")
    print(f"  Pre-earnings straddle: ${earnings['pre_earnings']['straddle_price']:.2f}")
    
    # ---- SECTION 5: Vanna & Charm ----
    print("\n" + "=" * 60)
    print("5. VANNA & CHARM (DEALER FLOW)")
    print("=" * 60)
    
    vc_analyzer = VannaCharmAnalysis()
    
    flower = vc_analyzer.vanna_flower_trade(S, sigma, r)
    print(f"\nVanna Flower Trade:")
    print(f"  {flower['structure']['short_straddle']}")
    print(f"  {flower['structure']['long_wings']}")
    print(f"  Net Gamma: {flower['greeks']['net_gamma']:.4f}")
    print(f"  Net Vanna: {flower['greeks']['net_vanna']:.4f}")
    
    # ---- SECTION 6: Portfolio Greeks ----
    print("\n" + "=" * 60)
    print("6. PORTFOLIO GREEKS MANAGEMENT")
    print("=" * 60)
    
    portfolio = PortfolioGreeks()
    portfolio.add_position(S, 95, 30/365, sigma, 5, 'put', 'long', 'Long 95P (protective)')
    portfolio.add_position(S, 105, 30/365, sigma, 3, 'call', 'short', 'Short 105C (covered call)')
    portfolio.add_position(S, 90, 60/365, sigma, 2, 'put', 'short', 'Short 90P (cash secured)')
    
    net = portfolio.get_net_greeks()
    print(f"\nPortfolio Net Greeks ({net['num_positions']} positions):")
    for greek, value in net['net_greeks'].items():
        print(f"  {greek.upper()}: {value:+.2f}")
    print(f"\nRisk Assessment:")
    for risk in net['risk_assessment']:
        print(f"  {risk}")
    
    # ---- SECTION 8: Model Comparison ----
    print("\n" + "=" * 60)
    print("8. PRICING MODEL COMPARISON")
    print("=" * 60)
    
    models = OptionsPricingModels.model_comparison(S, K, r, q, sigma, tau)
    print(f"\nModel Comparison (ATM Call):")
    for model, data in models.items():
        if model != 'comparison_notes':
            print(f"\n  {model.upper()}:")
            for key, value in data.items():
                print(f"    {key}: {value:.6f}")
    
    # ---- SECTION 9: Hedging ----
    print("\n" + "=" * 60)
    print("9. GREEKS HEDGING STRATEGIES")
    print("=" * 60)
    
    hedger = GreeksHedging()
    
    delta_hedge = hedger.delta_hedging(S, K, r, q, sigma, tau, 'call', contracts=10)
    print(f"\nDelta Hedge (10 ATM calls):")
    print(f"  Position delta: {delta_hedge['position_delta']:.0f}")
    print(f"  Shares to hedge: {delta_hedge['shares_to_hedge']:.0f}")
    print(f"  Hedge cost: ${delta_hedge['hedge_cost']:,.0f}")
    
    # ---- SECTION 10: Market Conditions ----
    print("\n" + "=" * 60)
    print("10. GREEKS IN DIFFERENT MARKET CONDITIONS")
    print("=" * 60)
    
    market = GreeksMarketConditions()
    
    vol_regimes = market.greeks_by_volatility_regime(S, K, r, q)
    print(f"\nGreeks by Volatility Regime:")
    for regime, data in vol_regimes.items():
        print(f"\n  {data['label']}:")
        print(f"    Price: ${data['price']:.4f}, Delta: {data['delta']:.4f}")
        print(f"    Gamma: {data['gamma']:.4f}, Theta/day: ${data['theta']:.4f}")
        print(f"    Vega: {data['vega']:.4f}")
    
    trending = market.trending_vs_rangebound_greeks(S, sigma, r, q)
    print(f"\nTrending vs Range-Bound:")
    for scenario, data in trending['scenarios'].items():
        print(f"\n  {scenario}:")
        print(f"    Realized vol: {data['realized_vol']:.0%}")
        print(f"    Best strategy: {data['best_strategy']}")
    
    print("\n" + "=" * 80)
    print("END OF COMPLETE OPTIONS GREEKS REFERENCE")
    print("=" * 80)


if __name__ == '__main__':
    main()
