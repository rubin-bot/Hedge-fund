import streamlit as st

st.set_page_config(page_title="Long-Short Equity Research", layout="wide")

st.title("Long-Short Equity Research Dashboard")

tab_portfolio, tab_factors, tab_risk, tab_simulation = st.tabs(
    ["Portfolio", "Factor Exposures", "Risk", "Paper Trading"]
)

with tab_portfolio:
    st.header("Current Portfolio")
    st.info("TODO: load current target weights from portfolio.construction and display holdings.")

with tab_factors:
    st.header("Factor Exposures")
    st.info("TODO: load factor scores from factors.scoring and plot exposures by name.")

with tab_risk:
    st.header("Risk & Stress Testing")
    st.info("TODO: surface risk.risk_management limit checks and risk.stress_testing scenario results.")

with tab_simulation:
    st.header("Paper Trading PnL")
    st.info("TODO: load simulation.paper_trading history and plot portfolio value over time.")
