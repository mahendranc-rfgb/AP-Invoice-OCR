from playwright.sync_api import sync_playwright
import time
import os
import subprocess

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        
        # Dashboard
        page.goto("http://127.0.0.1:8000/")
        page.wait_for_selector(".metric-box")
        time.sleep(2) # Allow charts to render
        page.screenshot(path="app/static/dash.png")
        print("Captured dashboard")
        
        # Document Queue / Invoice
        page.click("#nav-queue")
        page.wait_for_selector("#documents")
        time.sleep(1)
        page.screenshot(path="app/static/inv.png")
        print("Captured invoice view")
            
        browser.close()

if __name__ == "__main__":
    run()
