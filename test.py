from streamlit_js_eval import streamlit_js_eval
import streamlit as st
width = streamlit_js_eval(
    js_expressions="screen.width",
    key="SCR_WIDTH",
)

st.write(width)