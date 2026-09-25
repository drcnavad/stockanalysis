"""Technical indicators and per-indicator signals used by main_signal_analysis.ipynb.

Steps: calculate_technical_indicators (moving averages, RSI, MACD, Bollinger bands, ATR, OBV, Force Index)
-> one signal column per indicator (+1 buy / 0 / -1 sell) -> weighted_signal combines them into combined_signal.
All rescaling is point-in-time (trailing windows only), so a day never uses future data.
"""
import pandas as pd
import numpy as np

pd.options.display.float_format = '{:.2f}'.format
pd.set_option('display.max_columns', None)
# NOTE: no global warnings.filterwarnings("ignore") any more - it hid real pandas deprecations.


def apply_by_symbol(df, fn, ticker_col='Symbol'):
    """Run fn on each symbol's rows and concatenate (keeps the Symbol column; avoids the deprecated
    DataFrameGroupBy.apply-on-grouping-columns behaviour that drops it in pandas 3)."""
    if df.empty:
        return fn(df)
    return pd.concat([fn(g) for _, g in df.groupby(ticker_col, sort=False)], ignore_index=False)

# Point-in-time settings: every indicator below uses only data available at each row's date.
SCALE_WINDOW = 252      # trailing window (bars) for rescaling Force Index / OBV (expanding until full)
SCALE_MIN_PERIODS = 20
OBV_SLOPE_DAYS = 3
OBV_THRESHOLD = 1.0     # 3-day net signed volume must exceed 1x the 20-day average daily volume
BB_WINDOW = 20          # same window as bb_middle/bb_upper/bb_lower in calculate_technical_indicators


def pit_scale(series, window=SCALE_WINDOW, min_periods=SCALE_MIN_PERIODS):
    """Point-in-time rescale to -100..100: value / trailing max(|value|) over `window` bars.

    Replaces custom_scale() (which used the full-period min/max and therefore future data).
    """
    s = pd.Series(series, dtype=float)
    denom = s.abs().rolling(window, min_periods=min_periods).max()
    return (s / denom.replace(0, np.nan) * 100).clip(-100, 100)


def wilder_rsi(close, period=14):
    """RSI with Wilder smoothing; 100 when there are no losses, 50 when flat."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return rsi


def calculate_technical_indicators(df):
    """Indicators for ONE symbol (rows sorted by date). All values are point-in-time."""
    df = df.copy()
    # --- Moving Averages ---
    for window in [10, 30, 50, 100, 200]:
        df[f'ma_{window}'] = df['Close'].rolling(window=window).mean()

    # --- RSI (Wilder) ---
    df['rsi'] = wilder_rsi(df['Close'], 14)

    # --- ATR (Wilder, 14) for stops / volatility sizing ---
    prev_close = df['Close'].shift(1)
    true_range = pd.concat([df['High'] - df['Low'], (df['High'] - prev_close).abs(),
                            (df['Low'] - prev_close).abs()], axis=1).max(axis=1)
    df['atr'] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    # --- Bollinger Bands ---
    df['bb_middle'] = df['Close'].rolling(window=BB_WINDOW).mean()
    df['bb_std'] = df['Close'].rolling(window=BB_WINDOW).std()
    df['bb_upper'] = df['bb_middle'] + 2 * df['bb_std']
    df['bb_lower'] = df['bb_middle'] - 2 * df['bb_std']

    # --- MACD ---
    ema_12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema_12 - ema_26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    # --- Force Index ---
    # fi_raw keeps the sign/units; fi is rescaled point-in-time to -100..100 (trailing 252-bar max |fi|)
    df['fi_raw'] = (df['Close'].diff() * df['Volume']).ewm(span=13, adjust=False).mean()
    df['fi'] = pit_scale(df['fi_raw'])

    # --- OBV ---
    df['obv_std'] = (np.sign(df['Close'].diff()).fillna(0) * df['Volume']).cumsum()
    roll_min = df['obv_std'].rolling(SCALE_WINDOW, min_periods=SCALE_MIN_PERIODS).min()
    roll_max = df['obv_std'].rolling(SCALE_WINDOW, min_periods=SCALE_MIN_PERIODS).max()
    df['obv'] = ((df['obv_std'] - roll_min) / (roll_max - roll_min).replace(0, np.nan)).fillna(0.5)  # 0..1, trailing window
    avg_volume = df['Volume'].rolling(20, min_periods=5).mean()
    df['obv_slope'] = df['obv_std'].diff(OBV_SLOPE_DAYS) / avg_volume.replace(0, np.nan)  # in "average days of volume"

    # --- Adaptive Fibonacci levels from the most recent CONFIRMED swing high/low ---
    # A 3-bar fractal at bar j needs bar j+1 to exist, so it is only known from bar j+1 onward:
    # the swing value is placed on j+1 (shift(1)) and carried forward.
    high, low = df['High'], df['Low']
    is_swing_high = (high > high.shift(1)) & (high > high.shift(-1))
    is_swing_low = (low < low.shift(1)) & (low < low.shift(-1))
    last_high = high.where(is_swing_high).shift(1).ffill()
    last_low = low.where(is_swing_low).shift(1).ffill()
    last_high = last_high.fillna(high.cummax())
    last_low = last_low.fillna(low.cummin())

    diff = last_high - last_low
    df['fib_0%'] = last_high
    df['fib_23.6%'] = last_high - diff * 0.236
    df['fib_38.2%'] = last_high - diff * 0.382
    df['fib_50%'] = last_high - diff * 0.500
    df['fib_61.8%'] = last_high - diff * 0.618
    df['fib_76.4%'] = last_high - diff * 0.764
    df['fib_100%'] = last_low

    # --- Clean up --- (warm-up rows get 0; callers drop rows without a full ma_200)
    df = df.fillna(0)
    return df


def custom_scale(column):
    """DEPRECATED: scales with full-period min/max (uses future data). Kept for old notebooks;
    the pipeline now uses pit_scale() inside calculate_technical_indicators()."""
    scaled = np.zeros(len(column))
    neg_vals = column[column < 0]
    pos_vals = column[column > 0]
    if not neg_vals.empty:
        min_neg, max_neg = neg_vals.min(), neg_vals.max()
        scaled[column < 0] = (neg_vals - max_neg) / (min_neg - max_neg) * -100
    if not pos_vals.empty:
        min_pos, max_pos = pos_vals.min(), pos_vals.max()
        scaled[column > 0] = (pos_vals - min_pos) / (max_pos - min_pos) * 100
    return scaled


def generate_strict_signals(df):
    """Moving-average signals (+1 when the close is 1% above an MA, -1 when 1% below; MA50/100/200 also need the
    shorter MAs to agree) and the OBV momentum signal."""
    # --- MA Signals ---
    for ma in ['ma_10', 'ma_30', 'ma_50', 'ma_100', 'ma_200']:
        buffer = df[ma] * 0.01  # Increased to 1% buffer for stricter BUY
        df[f'signal_{ma}'] = np.where(df['Close'] > df[ma] + buffer, 1,
                                      np.where(df['Close'] < df[ma] - buffer, -1, 0))
        
        # Additional strictness: For longer MAs, require shorter MA alignment
        if ma == 'ma_50':
            # For MA50 BUY, require MA10 > MA30 (short-term uptrend)
            df.loc[(df['signal_ma_50'] == 1) & (df['ma_10'] <= df['ma_30']), 'signal_ma_50'] = 0
            # STRICT: For MA50 SELL, require MA10 < MA30 (short-term downtrend)
            df.loc[(df['signal_ma_50'] == -1) & (df['ma_10'] >= df['ma_30']), 'signal_ma_50'] = 0
        elif ma == 'ma_100':
            # For MA100 BUY, require MA30 > MA50 (medium-term uptrend)
            df.loc[(df['signal_ma_100'] == 1) & (df['ma_30'] <= df['ma_50']), 'signal_ma_100'] = 0
            # STRICT: For MA100 SELL, require MA30 < MA50 (medium-term downtrend)
            df.loc[(df['signal_ma_100'] == -1) & (df['ma_30'] >= df['ma_50']), 'signal_ma_100'] = 0
        elif ma == 'ma_200':
            # For MA200 BUY, require MA50 > MA100 (long-term uptrend)
            df.loc[(df['signal_ma_200'] == 1) & (df['ma_50'] <= df['ma_100']), 'signal_ma_200'] = 0
            # STRICT: For MA200 SELL, require MA50 < MA100 (long-term downtrend)
            df.loc[(df['signal_ma_200'] == -1) & (df['ma_50'] >= df['ma_100']), 'signal_ma_200'] = 0

    # --- OBV momentum ---
    # obv_slope = 3-day OBV change / 20-day average volume (computed in calculate_technical_indicators).
    # Threshold is in the same units (1.0 = one average day of net buying), not on a 0-100 rescale.
    if 'obv_slope' not in df.columns:
        df['obv_slope'] = df.groupby('Symbol')['obv'].transform(lambda s: s.diff().rolling(OBV_SLOPE_DAYS).sum())
    df['signal_obv'] = np.where(df['obv_slope'] > OBV_THRESHOLD, 1,
                                np.where(df['obv_slope'] < -OBV_THRESHOLD, -1, 0))

    return df

def rsi_signals(group, lower=20, upper=85, ma_period=3, use_trend=True, oversold_threshold=30):
    """RSI signals for one symbol: +1 when RSI is oversold (< 30) and the price is at/above its short MA,
    -1 when RSI is overbought (> 70) and the price is at/below it."""
    group = group.copy()
    group['rsi_signal'] = 0
    
    # Rolling MA trend filter
    if use_trend:
        group['ma'] = group['Close'].rolling(ma_period).mean()  # no bfill (would use future bars)
    else:
        group['ma'] = group['Close'] * 0 + 1  # all True
    
    # --- BUY ---
    # STRICT: RSI oversold in uptrend - price must be above MA (not in downtrend)
    buy_condition = group['rsi'] < lower  # RSI < 20
    trend_buy = group['Close'] > group['ma']  # Price above short-term MA (uptrend)
    group.loc[buy_condition & trend_buy, 'rsi_signal'] = 1
    
    # STRICT: Moderate oversold (RSI 20-30) but ONLY if price is at least at MA level (not below)
    # Removed the loose conditions that allowed buying 20-30% below MA
    moderate_oversold = (group['rsi'] < oversold_threshold) & (group['rsi'] >= 20)
    price_at_ma = group['Close'] >= group['ma']  # Price at or above MA (strict)
    group.loc[moderate_oversold & price_at_ma, 'rsi_signal'] = 1
    
    # --- SELL ---
    # STRICT: RSI overbought in downtrend - price must be below MA (not in uptrend)
    sell_condition = group['rsi'] > upper  # RSI > 85
    trend_sell = group['Close'] < group['ma']  # Price below short-term MA (downtrend)
    group.loc[sell_condition & trend_sell, 'rsi_signal'] = -1
    
    # STRICT: Moderate overbought (RSI 70-85) but ONLY if price is at or below MA level (not above)
    # This catches overbought conditions in downtrends
    moderate_overbought = (group['rsi'] > 70) & (group['rsi'] <= upper)
    price_at_or_below_ma = group['Close'] <= group['ma']  # Price at or below MA (strict)
    group.loc[moderate_overbought & price_at_or_below_ma, 'rsi_signal'] = -1
    
    return group.drop(columns=['ma'])


def fi_signals_strict(df, lookback=3, min_fi=2):
    """
    fi_signal = 1 for buy, -1 for sell, 0 for hold.
    lookback = number of consecutive FI values needed
    min_fi = minimum absolute FI value to count toward streak
    """
    df = df.copy()
    df['fi_signal'] = 0

    # Only consider FI values above threshold
    df['fi_direction'] = df['fi'].apply(lambda x: 1 if x >= min_fi else -1 if x <= -min_fi else 0)
    
    # Compute streaks
    df['fi_streak'] = df['fi_direction'].groupby((df['fi_direction'] != df['fi_direction'].shift()).cumsum()).cumcount() + 1
    
    # STRICT: For BUY, require price to be in uptrend (Close > MA10) to avoid buying in downtrends
    # Calculate MA10 for trend confirmation if not already present
    ma_10_was_present = 'ma_10' in df.columns
    if not ma_10_was_present:
        df['ma_10'] = df['Close'].rolling(window=10).mean()
    
    # Buy after lookback consecutive positives AND price above MA10 (uptrend confirmation)
    buy_condition = (df['fi_direction'] == 1) & (df['fi_streak'] >= lookback)
    trend_confirmation = df['Close'] > df['ma_10']  # Price in uptrend
    df.loc[buy_condition & trend_confirmation, 'fi_signal'] = 1
    
    # STRICT: Sell after lookback consecutive negatives AND price below MA10 (downtrend confirmation)
    sell_condition = (df['fi_direction'] == -1) & (df['fi_streak'] >= lookback)
    downtrend_confirmation = df['Close'] < df['ma_10']  # Price in downtrend
    df.loc[sell_condition & downtrend_confirmation, 'fi_signal'] = -1

    # Clean up helper columns (drop ma_10 only if we created it)
    if not ma_10_was_present and 'ma_10' in df.columns:
        df = df.drop(columns=['ma_10'])
    df = df.drop(columns=["fi_direction", "fi_streak"], errors='ignore')
    return df

def bollinger_signal_middle(df, bb_window=BB_WINDOW, rsi_col='rsi', close_col='Close', ma_period=20, ticker_col='Symbol'):
    """
    Generates BB+RSI signals using middle band as trend filter.
    Now also detects oversold conditions when price is at/below lower Bollinger band.
    """
    def bb_middle_group(group):
        """Bollinger middle-band signals for one symbol."""
        group = group.copy()
        
        # Bollinger Bands (recalculate to ensure we have lower band)
        group['bb_middle'] = group[close_col].rolling(bb_window).mean()
        group['bb_std'] = group[close_col].rolling(bb_window).std()
        group['bb_lower'] = group['bb_middle'] - 2 * group['bb_std']
        group['bb_upper'] = group['bb_middle'] + 2 * group['bb_std']
        
        # Buy: price above middle band + RSI in healthy range (not overbought)
        # STRICT: RSI must be < 60 (not just < 70) to avoid buying in overbought conditions
        group['bb_signal'] = 0
        healthy_rsi = (group[rsi_col] < 60) & (group[rsi_col] > 30)  # RSI in healthy range
        price_above_middle = group[close_col] > group['bb_middle']
        group.loc[price_above_middle & healthy_rsi, 'bb_signal'] = 1
        
        # Additional buy signal: price at or below lower Bollinger band + RSI oversold
        # STRICT: RSI must be < 30 (not < 35) for stronger oversold confirmation
        oversold_bb = (group[close_col] <= group['bb_lower']) & (group[rsi_col] < 30)
        group.loc[oversold_bb, 'bb_signal'] = 1
        
        # STRICT: Sell: price below middle band + RSI in overbought range (not just > 30)
        # Require RSI > 50 to ensure we're selling in overbought conditions, not just neutral
        overbought_rsi = group[rsi_col] > 50  # RSI in overbought range
        price_below_middle = group[close_col] < group['bb_middle']
        group.loc[price_below_middle & overbought_rsi, 'bb_signal'] = -1
        
        # Additional sell signal: price at or above upper Bollinger band + RSI overbought
        # STRICT: RSI must be > 70 (not just > 50) for stronger overbought confirmation
        overbought_bb = (group[close_col] >= group['bb_upper']) & (group[rsi_col] > 70)
        group.loc[overbought_bb, 'bb_signal'] = -1
        
        return group.drop(columns=['bb_middle', 'bb_std', 'bb_lower', 'bb_upper'])
    
    return apply_by_symbol(df, bb_middle_group, ticker_col)

def macd_signals(group):
    """MACD signals for one symbol: +1 on a cross above the signal line with MACD rising, -1 on a cross below with MACD falling."""
    group = group.copy()
    group['macd_trade'] = 0
    
    # MACD crossover conditions
    cross_up = (group['macd'].shift(1) < group['macd_signal'].shift(1)) & (group['macd'] >= group['macd_signal'])
    cross_down = (group['macd'].shift(1) > group['macd_signal'].shift(1)) & (group['macd'] <= group['macd_signal'])
    
    # STRICT: For BUY, require MACD histogram to be positive (MACD > Signal) and increasing
    # This ensures we're buying on confirmed bullish momentum, not just a weak crossover
    macd_positive = group['macd'] > group['macd_signal']  # Histogram positive
    macd_increasing = group['macd'] > group['macd'].shift(1)  # MACD line increasing
    
    group.loc[cross_up & macd_positive & macd_increasing, 'macd_trade'] = 1
    
    # STRICT: For SELL, require MACD histogram to be negative (MACD < Signal) and decreasing
    # This ensures we're selling on confirmed bearish momentum, not just a weak crossover
    macd_negative = group['macd'] < group['macd_signal']  # Histogram negative
    macd_decreasing = group['macd'] < group['macd'].shift(1)  # MACD line decreasing
    
    group.loc[cross_down & macd_negative & macd_decreasing, 'macd_trade'] = -1
    
    return group

def fibonacci_signals(df, close_col='Close'):
    """
    Generates signals based on price position relative to Fibonacci retracement levels.
    Buy signal when price is near key support levels (fib_61.8%, fib_50%, fib_38.2%).
    Sell signal when price is near resistance levels (fib_0%, fib_23.6%).
    """
    df = df.copy()
    df['fib_signal'] = 0
    
    # Calculate distance from each Fibonacci level (as percentage)
    tolerance = 0.015  # Reduced to 1.5% tolerance for stricter matching
    
    # STRICT: Require trend confirmation - price should be above MA50 for BUY signals
    # This ensures we're buying at support in an uptrend, not in a downtrend
    ma_50_was_present = 'ma_50' in df.columns
    if not ma_50_was_present:
        df['ma_50'] = df[close_col].rolling(window=50).mean()
    
    # Buy signals: Price near support levels (fib_61.8%, fib_50%, fib_38.2%)
    for fib_level in ['fib_61.8%', 'fib_50%', 'fib_38.2%']:
        if fib_level in df.columns:
            distance = abs((df[close_col] - df[fib_level]) / df[fib_level])
            # Buy when price is near support and potentially bouncing up
            near_support = distance <= tolerance
            price_above_fib = df[close_col] >= df[fib_level] * 0.98  # Allow slight below
            # STRICT: Require price above MA50 (uptrend) to avoid buying in downtrends
            uptrend_confirmation = df[close_col] > df['ma_50']
            df.loc[near_support & price_above_fib & uptrend_confirmation, 'fib_signal'] = 1
    
    # Sell signals: Price near resistance levels (fib_0%, fib_23.6%)
    for fib_level in ['fib_0%', 'fib_23.6%']:
        if fib_level in df.columns:
            distance = abs((df[close_col] - df[fib_level]) / df[fib_level])
            # Sell when price is near resistance and potentially reversing
            near_resistance = distance <= tolerance
            price_below_fib = df[close_col] <= df[fib_level] * 1.02  # Allow slight above
            # STRICT: Require price below MA50 (downtrend) to avoid selling in uptrends
            # Note: ma_50 is still available here since we haven't dropped it yet
            downtrend_confirmation = df[close_col] < df['ma_50']
            df.loc[near_resistance & price_below_fib & downtrend_confirmation, 'fib_signal'] = -1
    
    # Clean up: drop ma_50 only if we created it (after both BUY and SELL signals are processed)
    if not ma_50_was_present and 'ma_50' in df.columns:
        df = df.drop(columns=['ma_50'])
    
    return df






def weighted_signal(df, weights=None, signal_cols=None, final_col='combined_signal'):
    """
    Combine multiple signals with given weights into a final score.
    Arguments:
        df: dataframe with signal columns
        weights: dict of {column_name: weight_in_percent}
        signal_cols: list of columns to include (optional, inferred from weights if None)
        final_col: name of output column
    Returns:
        df with new weighted score column
    """
    df = df.copy()
    
    # Default weights if not provided
    # Optimized based on strictness updates and signal reliability
    if weights is None:
        weights = {
            # Moving Averages - Trend indicators (Total: 38)
            # Higher weights for MAs with trend alignment confirmations
            'signal_ma_10': 3,      # Short-term, no alignment → Lower weight (less reliable)
            'signal_ma_30': 6,      # Medium-term, no alignment → Moderate weight
            'signal_ma_50': 10,     # Medium-term WITH MA10>MA30 alignment → High weight (very reliable)
            'signal_ma_100': 8,     # Long-term WITH MA30>MA50 alignment → High weight (very reliable)
            'signal_ma_200': 11,    # Major trend WITH MA50>MA100 alignment → Highest weight (most reliable)
            
            # Momentum Indicators (Total: 20)
            # High weights for momentum indicators with trend confirmations
            'rsi_signal': 10,       # Trend-confirmed RSI → Very high weight (most reliable momentum)
            'macd_trade': 4,        # Crossovers lagged (pointed the wrong way in 1-year check) → Low weight
            'fi_signal': 6,         # Streak + trend confirmed → Moderate weight (good reliability)
            
            # Volume & Volatility (Total: 7)
            # Moderate weights - important but may generate fewer signals due to strictness
            'signal_obv': 4,        # Threshold-based, might be too strict → Lower weight
            'bb_signal': 3,         # RSI-filtered BB (pointed the wrong way in 1-year check) → Low weight
            
            # Support/Resistance (Total: 3)
            # Lower weight - strict conditions may generate fewer signals
            'fib_signal': 3         # Trend-confirmed Fibonacci (weak in 1-year check) → Low weight
            }
    
    if signal_cols is None:
        signal_cols = list(weights.keys())
    
    # Normalize weights to sum to 100
    total_weight = sum(weights.values())
    norm_weights = {k: v/total_weight for k,v in weights.items()}
    
    # Compute weighted score
    df[final_col] = 0.0
    
    for col in signal_cols:
        if col not in df.columns:
            continue
            
        signal_value = df[col].fillna(0)
        weight = norm_weights[col] * 100
        df[final_col] += signal_value * weight
    
    return df
