import os
import glob
import asyncio
import random
import time
import shutil
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

# --- 🔥 USER SETTINGS ---
# اگر یہ True ہے تو ہر سٹیپ کی تصویر بنے گی۔
# اگر یہ False ہے تو کوئی بھی تصویر نہیں بنے گی (Data بچانے کے لیے)۔
live_logs = True 

# --- CONFIG ---
CAPTURE_DIR = "./captures"
NUMBERS_FILE = "numbers.txt"
SUCCESS_FILE = "success.txt"
FAILED_FILE = "failed.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id5.cloud.huawei.com"

app = FastAPI()
if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

# File Init
for f in [NUMBERS_FILE, SUCCESS_FILE, FAILED_FILE, PROXY_FILE]:
    if not os.path.exists(f): open(f, 'w').close()

# --- CAPTCHA SOLVER IMPORT ---
try:
    from captcha_solver import solve_captcha
except ImportError:
    async def solve_captcha(page, session_id, logger=print): return False

SETTINGS = {"country": "Russia", "proxy_manual": ""}
BOT_RUNNING = False
logs = []
PROXY_INDEX = 0 

# --- HELPERS ---
def log_msg(message, level="step"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 500: logs.pop()

def save_data(filename, data):
    with open(filename, "a", encoding="utf-8") as f: f.write(f"{data}\n")

def get_next_number():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f: lines = f.read().splitlines()
        valid = [l.strip() for l in lines if l.strip()]
        if valid: return valid[0]
    return None

def remove_number(number):
    if not os.path.exists(NUMBERS_FILE): return
    with open(NUMBERS_FILE, "r") as f: lines = f.readlines()
    with open(NUMBERS_FILE, "w") as f:
        for line in lines:
            if line.strip() != number: f.write(line)

def count_lines(filename):
    if not os.path.exists(filename): return 0
    with open(filename, "r") as f: return len([l for l in f if l.strip()])

# --- PROXY LOGIC ---
def parse_proxy_string(proxy_str):
    if not proxy_str or len(proxy_str) < 5: return None
    p = proxy_str.strip()
    if p.count(":") == 3 and "://" not in p:
        parts = p.split(":")
        return {"server": f"http://{parts[0]}:{parts[1]}", "username": parts[2], "password": parts[3]}
    if "://" not in p: p = f"http://{p}"
    try:
        parsed = urlparse(p)
        cfg = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username: cfg["username"] = parsed.username
        if parsed.password: cfg["password"] = parsed.password
        return cfg
    except: return None

def get_sequential_proxy():
    global PROXY_INDEX
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"]) > 5:
        return parse_proxy_string(SETTINGS["proxy_manual"])
    
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                if PROXY_INDEX >= len(lines): PROXY_INDEX = 0 
                selected_proxy = lines[PROXY_INDEX]
                PROXY_INDEX += 1 
                return parse_proxy_string(selected_proxy)
        except: pass
    return None 

# --- VISUALS ---
async def capture_step(page, step_name, wait_time=0, force=False):
    # 🔥 STRICT CHECK: اگر live_logs آف ہے، تو کچھ بھی کیپچر نہ ہو۔
    if not live_logs: return 
    
    if not BOT_RUNNING: return
    if wait_time > 0: await asyncio.sleep(wait_time)
    timestamp = datetime.now().strftime("%H%M%S")
    filename = f"{CAPTURE_DIR}/{timestamp}_{step_name}.jpg"
    try: await page.screenshot(path=filename)
    except: pass

async def show_red_dot(page, x, y):
    # یہ صرف ویژول ہے، اس کا ڈیٹا یا کیپچر سے تعلق نہیں، لیکن سیفٹی کے لیے ٹرائی کیچ میں ہے۔
    try:
        await page.evaluate(f"""
            var dot = document.createElement('div');
            dot.style.position = 'absolute'; 
            dot.style.left = '{x-15}px'; dot.style.top = '{y-15}px';
            dot.style.width = '30px'; dot.style.height = '30px'; 
            dot.style.background = 'rgba(255, 0, 0, 0.7)'; 
            dot.style.borderRadius = '50%'; dot.style.zIndex = '999999'; 
            dot.style.pointerEvents = 'none'; dot.style.border = '3px solid white'; 
            document.body.appendChild(dot);
            setTimeout(() => {{ dot.remove(); }}, 1500);
        """)
    except: pass

# --- CLICK LOGIC ---
async def click_element(page, finder, name):
    try:
        el = finder()
        if await el.count() > 0:
            try: await el.first.scroll_into_view_if_needed()
            except: pass
            
            box = await el.first.bounding_box()
            if box:
                cx = box['x'] + box['width'] / 2
                cy = box['y'] + box['height'] / 2
                
                log_msg(f"🖱️ Tapping {name}...", level="step")
                if live_logs: await show_red_dot(page, cx, cy) # صرف تب ڈاٹ دکھائے جب لاگز آن ہوں
                await asyncio.sleep(0.3)
                await page.touchscreen.tap(cx, cy)
                return True
        return False
    except: return False

# 🔥 SMART ACTION 🔥
async def smart_action(page, finder, verifier, step_name, wait_after=5):
    if not BOT_RUNNING: return False
    
    log_msg(f"🔍 Action: {step_name}...", level="step")
    await capture_step(page, f"Pre_{step_name}")

    for attempt in range(1, 4): 
        if not BOT_RUNNING: return False
        
        if step_name != "Register_Text":
            if verifier and await verifier().count() > 0:
                log_msg(f"✅ {step_name} Already Done.", level="step")
                return True

        clicked = await click_element(page, finder, f"{step_name} (Try {attempt})")
        
        if clicked:
            await capture_step(page, f"Click_{step_name}_{attempt}")
            log_msg(f"⏳ Waiting {wait_after}s...", level="step")
            await asyncio.sleep(wait_after)
            
            await capture_step(page, f"Post_{step_name}_{attempt}")

            if verifier and await verifier().count() > 0:
                log_msg(f"✅ {step_name} Success!", level="step")
                return True
            elif await finder().count() > 0:
                log_msg(f"⚠️ {step_name} click failed. Retrying...", level="step")
                continue 
            else:
                log_msg(f"⏳ Loading... Waiting 5s...", level="step")
                await asyncio.sleep(5)
                if verifier and await verifier().count() > 0:
                    log_msg(f"✅ {step_name} Success (After Load)!", level="step")
                    return True
                else:
                    log_msg(f"⚠️ Stuck / Loading...", level="step")
                    await capture_step(page, f"Stuck_{step_name}")
        else:
            log_msg(f"❌ {step_name} Not Found (Attempt {attempt})", level="step")
            await asyncio.sleep(2)

    return False

# --- WORKER ---
async def master_loop():
    global BOT_RUNNING
    
    log_msg("🟢 Worker Started.", level="main")
    
    while BOT_RUNNING:
        current_number = get_next_number()
        if not current_number:
            log_msg("ℹ️ No Numbers.", level="main"); BOT_RUNNING = False; break
            
        proxy_cfg = get_sequential_proxy()
        p_display = proxy_cfg['server'] if proxy_cfg else "🌐 Direct Internet"
        
        log_msg(f"🔵 Processing: {current_number} | Using: {p_display}", level="main") 
        
        try:
            res = await run_session(current_number, SETTINGS["country"], proxy_cfg)
            if res == "success":
                log_msg("🎉 Verified!", level="main")
                save_data(SUCCESS_FILE, current_number)
                remove_number(current_number)
            elif res == "failed":
                log_msg("❌ Failed (Hard Skip).", level="main")
                save_data(FAILED_FILE, current_number)
                remove_number(current_number)
            
        except Exception as e:
            log_msg(f"🔥 Crash: {e}", level="main")
        
        await asyncio.sleep(2)

async def run_session(phone, country, proxy):
    # ڈیٹا ٹریک کرنے کے لیے
    network_usage = {"bytes": 0}
    
    # 🔥 SMART SWITCH: تصاویر کو کنٹرول کرنے کا بٹن
    # شروع میں یہ True ہے، مطلب تصاویر بلاک رہیں گی۔
    state = {"block_images": True} 

    def print_stats(status_label):
        total_kb = network_usage["bytes"] / 1024
        total_mb = total_kb / 1024
        log_msg(f"📉 {status_label} | Data: {total_kb:.2f} KB ({total_mb:.3f} MB)", level="main")

    try:
        async with async_playwright() as p:
            launch_args = {
                "headless": True, 
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--ignore-certificate-errors", "--disable-web-security"]
            }
            if proxy: launch_args["proxy"] = proxy 

            log_msg("🚀 Launching (CSS Allowed, Images Smart Block)...", level="step")
            try: browser = await p.chromium.launch(**launch_args)
            except Exception as e: log_msg(f"❌ Proxy Fail: {e}", level="main"); return "retry"

            pixel_5 = p.devices['Pixel 5'].copy()
            pixel_5['viewport'] = {'width': 412, 'height': 950}
            pixel_5['has_touch'] = True 
            
            context = await browser.new_context(**pixel_5, locale="en-US", ignore_https_errors=True)
            page = await context.new_page()

            # --- 🔥 NETWORKING LOGIC 🔥 ---
            async def handle_route(route):
                try:
                    r_type = route.request.resource_type
                    
                    # 1. صرف فونٹس اور میڈیا (ویڈیو/آڈیو) کو بلاک کریں
                    # CSS (stylesheet) کو یہاں سے نکال دیا ہے تاکہ پیج وائٹ نہ ہو۔
                    if r_type in ["font", "media"]:
                        await route.abort()
                        return

                    # 2. تصاویر کا فیصلہ اسٹیج کے حساب سے ہوگا
                    if r_type == "image":
                        if state["block_images"]:
                            # جب تک DOB نہیں ہوتا، ہر تصویر بلاک (Data Saved!)
                            await route.abort()
                            return
                        else:
                            # DOB کے بعد تصاویر اوپن (کیپچا کے لیے)
                            await route.continue_()
                            return

                    # باقی سب (JS, CSS, HTML, XHR) جانے دو
                    await route.continue_()
                except: 
                    try: await route.continue_()
                    except: pass

            async def count_data(request):
                try:
                    sizes = await request.sizes()
                    usage = (sizes.get('requestHeadersSize', 0) + sizes.get('requestBodySize', 0) + 
                             sizes.get('responseHeadersSize', 0) + sizes.get('responseBodySize', 0))
                    network_usage["bytes"] += usage
                except: pass

            # راؤٹنگ اور ٹریکنگ آن کریں
            await page.route("**/*", handle_route)
            page.on("requestfinished", count_data)


            # --- STEP 1: LOAD URL ---
            log_msg("🌐 Opening URL...", level="step")
            try:
                if not BOT_RUNNING: print_stats("STOPPED"); await browser.close(); return "stopped"
                await page.goto(BASE_URL, timeout=60000)
                log_msg("⏳ Page Load Wait (3s)...", level="step")
                await asyncio.sleep(3) 
                await capture_step(page, "01_Loaded")
            except: 
                print_stats("TIMEOUT"); await browser.close(); return "retry"

            # --- STEP 2: REGISTER ---
            if not await smart_action(page, lambda: page.get_by_text("Register", exact=True), lambda: page.get_by_text("Stay informed", exact=False), "Register_Text", wait_after=3): 
                print_stats("FAIL: Register"); await browser.close(); return "retry"

            # --- STEP 3: AGREE ---
            cb = page.get_by_text("Stay informed", exact=False)
            if await cb.count() > 0:
                await click_element(page, lambda: cb, "Stay Informed Checkbox")
                await asyncio.sleep(0.5)
            
            if not await smart_action(page, lambda: page.get_by_text("Agree", exact=False).last, lambda: page.get_by_text("Date of birth", exact=False), "Agree_Last", wait_after=3): 
                print_stats("FAIL: Agree"); await browser.close(); return "retry"

            # --- STEP 4: DOB (CRITICAL POINT) ---
            if not await smart_action(page, lambda: page.get_by_text("Next", exact=False).last, lambda: page.get_by_text("Use phone number", exact=False), "DOB_Next_Text", wait_after=3): 
                print_stats("FAIL: DOB"); await browser.close(); return "retry"
            
            # 🔥🔥🔥 IMAGE UNLOCKER 🔥🔥🔥
            # یہاں DOB ہو گیا ہے، اب ہم تصاویر کھول دیں گے تاکہ آگے کیپچا نظر آئے
            log_msg("🔓 DOB Passed: Enabling Images for Captcha...", level="step")
            state["block_images"] = False 


            # --- STEP 5: PHONE TAB ---
            if not await smart_action(page, lambda: page.get_by_text("Use phone number", exact=False), lambda: page.get_by_text("Country/Region"), "UsePhone_Text", wait_after=3): 
                print_stats("FAIL: Phone Tab"); await browser.close(); return "retry"

            # --- STEP 6: COUNTRY ---
            log_msg(f"🌍 Selecting {country}...", level="step")
            
            if not await smart_action(page, lambda: page.get_by_text("Hong Kong", exact=False).or_(page.locator(".arrow-icon").first), lambda: page.get_by_placeholder("Search", exact=False), "Open_Country_List", wait_after=2): 
                print_stats("FAIL: Country List"); await browser.close(); return "retry"

            search = page.get_by_placeholder("Search", exact=False).first
            await search.click(); await page.keyboard.type(country, delay=50); await asyncio.sleep(2)
            await capture_step(page, "04_Country_Typed")
            
            matches = page.get_by_text(country, exact=False)
            if await matches.count() > 0:
                await click_element(page, lambda: matches.first, f"Country: {country}"); await asyncio.sleep(2) 
            else:
                log_msg("❌ Country Not Found", level="main"); print_stats("FAIL: No Country"); await browser.close(); return "retry"

            # --- STEP 7: INPUT PHONE ---
            inp = page.locator("input[type='tel']").first
            if await inp.count() == 0: inp = page.locator("input").first
            
            if await inp.count() > 0:
                clean_phone = phone
                if country == "Russia" and clean_phone.startswith("7"): clean_phone = clean_phone[1:] 
                elif country == "Pakistan" and clean_phone.startswith("92"): clean_phone = clean_phone[2:] 
                
                log_msg(f"🔢 Inputting: {clean_phone}", level="step")
                await inp.click()
                for c in clean_phone:
                    if not BOT_RUNNING: break
                    await page.keyboard.type(c); await asyncio.sleep(0.05)
                
                if live_logs: await show_red_dot(page, 350, 100)
                await page.touchscreen.tap(350, 100) 
                await capture_step(page, "05_Filled")
                
                # --- STEP 8: GET CODE ---
                get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code"))
                if await get_code.count() > 0:
                    await click_element(page, lambda: get_code.first, "Get Code Button")
                    log_msg("⏳ Waiting for Response...", level="main")
                    
                    # یہاں تصاویر اب آن ہیں، تو کیپچا لوڈ ہونا چاہیے
                    await asyncio.sleep(5); await capture_step(page, "06_Check_Resp")

                    if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                        log_msg("⛔ FATAL: System Error", level="main")
                        print_stats("FATAL ERROR"); await browser.close(); return "failed"

                    result = "failed"
                    start_solve_time = time.time()
                    while BOT_RUNNING:
                        if time.time() - start_solve_time > 60: break

                        if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                            log_msg("🧩 CAPTCHA FOUND!", level="main")
                            await capture_step(page, "08_Captcha_Found")
                            session_id = f"sess_{int(time.time())}"
                            ai_success = await solve_captcha(page, session_id, logger=lambda m: log_msg(m, level="step"))
                            if not ai_success: log_msg("⚠️ Solver Failed", level="step"); result = "retry"; break
                            await asyncio.sleep(5)
                            if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                                log_msg("✅ CAPTCHA SOLVED!", level="main"); await capture_step(page, "Success_Solved"); result = "success"; break
                            else: continue
                        
                        if await page.get_by_text("sent", exact=False).count() > 0:
                            log_msg("✅ CODE SENT!", level="main"); await capture_step(page, "Success_Direct"); result = "success"; break
                        
                        log_msg("❌ Checking...", level="step"); await asyncio.sleep(2)

                    print_stats(f"DONE ({result})"); await browser.close(); return result
                else:
                    log_msg("❌ Get Code Missing", level="step"); print_stats("FAIL: No Button"); await browser.close(); return "retry"

            await browser.close(); return "retry"

    except Exception as e:
        log_msg(f"❌ Error: {str(e)}", level="main"); return "retry"

# --- API ENDPOINTS ---
@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)[:10]
    images = [f"/captures/{os.path.basename(f)}" for f in files]
    
    p_check = get_sequential_proxy() 
    p_disp = p_check['server'] if p_check else "🌐 Direct Internet"
    
    stats = {
        "remaining": count_lines(NUMBERS_FILE),
        "success": count_lines(SUCCESS_FILE),
        "failed": count_lines(FAILED_FILE)
    }
    return JSONResponse({
        "logs": logs[:50], 
        "images": images, 
        "running": BOT_RUNNING, 
        "current_country": SETTINGS["country"], 
        "current_proxy": p_disp,
        "stats": stats
    })

@app.get("/download/{ftype}")
async def download_file(ftype: str):
    fname = f"{ftype}.txt"
    if os.path.exists(fname): return FileResponse(fname, filename=fname)
    return {"error": "File not found"}

@app.post("/clear_data")
async def clear_data():
    global logs
    logs = []
    open(NUMBERS_FILE, 'w').close()
    open(SUCCESS_FILE, 'w').close()
    open(FAILED_FILE, 'w').close()
    for f in glob.glob(f'{CAPTURE_DIR}/*'): os.remove(f)
    return {"status": "cleared"}

@app.post("/update_settings")
async def update_settings(country: str = Form(...), manual_proxy: Optional[str] = Form("")):
    SETTINGS["country"] = country
    SETTINGS["proxy_manual"] = manual_proxy
    return {"status": "updated"}

@app.post("/upload_proxies")
async def upload_proxies(file: UploadFile = File(...)):
    with open(PROXY_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    count = count_lines(PROXY_FILE)
    log_msg(f"🌐 Proxies Uploaded: {count}", level="main")
    return {"status": "saved"}

@app.post("/upload_numbers")
async def upload_numbers(file: UploadFile = File(...)):
    with open(NUMBERS_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    return {"status": "saved"}

@app.post("/start")
async def start_bot(bt: BackgroundTasks):
    global BOT_RUNNING
    if not BOT_RUNNING:
        BOT_RUNNING = True
        bt.add_task(master_loop)
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 STOP COMMAND RECEIVED.", level="main")
    return {"status": "stopping"}
