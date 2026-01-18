import os, json
from datetime import datetime, timedelta, date
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_apscheduler import APScheduler
from werkzeug.utils import secure_filename
from sqlalchemy import func

app = Flask(__name__)
app.secret_key = "secret_key_inventory"
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['RECORDS_FOLDER'] = 'static/records'

db = SQLAlchemy(app)
scheduler = APScheduler()

for folder in [app.config['UPLOAD_FOLDER'], app.config['RECORDS_FOLDER']]:
    if not os.path.exists(folder): os.makedirs(folder)

# --- Models (保持不變) ---
class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    products = db.relationship('Product', backref='category', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    image = db.Column(db.String(100))
    cost = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    is_deleted = db.Column(db.Boolean, default=False)
    sales = db.relationship('Sale', backref='product', lazy=True)

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    profit = db.Column(db.Float, nullable=False)
    revenue = db.Column(db.Float, default=0.0)
    timestamp = db.Column(db.DateTime, default=datetime.now)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)

# --- 報表生成邏輯 (兩部分: 日報表Json 與 月報表Html) ---

# 1. 每日 JSON 報表 (保持不變)
def generate_json_report(target_date):
    start_of_day = datetime.combine(target_date, datetime.min.time())
    end_of_day = datetime.combine(target_date, datetime.max.time())
    sales_today = Sale.query.filter(Sale.timestamp >= start_of_day, Sale.timestamp <= end_of_day).all()
    
    hourly_data = [0] * 24
    for s in sales_today: hourly_data[s.timestamp.hour] += s.quantity
    
    detail_list = []
    item_summary = {}
    total_profit = 0
    total_revenue = 0
    
    for s in sales_today:
        rev = s.revenue if s.revenue is not None else 0
        detail_list.append({
            "time": s.timestamp.strftime("%H:%M:%S"),
            "product": s.product.name,
            "qty": s.quantity,
            "profit": round(s.profit, 2),
            "revenue": round(rev, 2)
        })
        total_profit += s.profit
        total_revenue += rev
        if s.product.name not in item_summary: item_summary[s.product.name] = {"qty": 0, "profit": 0, "revenue": 0}
        item_summary[s.product.name]["qty"] += s.quantity
        item_summary[s.product.name]["profit"] += s.profit
        item_summary[s.product.name]["revenue"] += rev

    report_data = {
        "date": target_date.strftime("%Y-%m-%d"),
        "summary": {"total_profit": round(total_profit, 2), "total_revenue": round(total_revenue, 2), "total_sales_count": sum(hourly_data)},
        "hourly_chart": hourly_data,
        "item_summary": item_summary,
        "raw_sales": detail_list
    }
    
    filename = f"daily_{target_date.strftime('%Y%m%d')}.json"
    with open(os.path.join(app.config['RECORDS_FOLDER'], filename), 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=4)
    return filename

# 2. [新] 每月 HTML 報表
# app.py 裡的 generate_monthly_html_report 函式

def generate_monthly_html_report(year, month):
    # 1. 計算該月的起始與結束時間
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        end_date = datetime(year, month + 1, 1) - timedelta(seconds=1)

    # 2. 查詢該月所有銷售
    sales = Sale.query.filter(Sale.timestamp >= start_date, Sale.timestamp <= end_date).all()
    
    # 3. 統計數據
    total_profit = 0
    total_revenue = 0
    total_qty = 0
    product_stats = {} 

    for s in sales:
        rev = s.revenue if s.revenue is not None else 0
        total_profit += s.profit
        total_revenue += rev
        total_qty += s.quantity
        
        if s.product.name not in product_stats:
            product_stats[s.product.name] = {"profit": 0, "qty": 0}
        product_stats[s.product.name]["profit"] += s.profit
        product_stats[s.product.name]["qty"] += s.quantity
        
    # 準備給圖表的數據 (圖表還是需要純數字，所以這裡保持 float/int)
    labels = list(product_stats.keys())
    profit_data = [round(v["profit"], 2) for v in product_stats.values()]
    qty_data = [v["qty"] for v in product_stats.values()]

    # 4. 渲染 HTML
    # [修改重點] 這裡我們把數字轉成漂亮的字串格式再傳進去
    html_content = render_template(
        'monthly_template.html',
        year=year,
        month=month,
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M'),
        
        # 這裡改用 int() 去掉小數點，並加上千分位逗號
        total_revenue=f"{int(total_revenue):,}",  
        total_profit=f"{int(total_profit):,}",
        total_qty=f"{int(total_qty):,}",
        
        labels=labels,
        profit_data=profit_data,
        qty_data=qty_data
    )
    
    filename = f"monthly_{year}_{month:02d}.html"
    filepath = os.path.join(app.config['RECORDS_FOLDER'], filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)
        
    return filename

# --- 排程任務 ---

# 任務1: 每天存日報表
@scheduler.task('cron', id='daily_job', day='*', hour='0', minute='0')
def auto_save_daily_report():
    with app.app_context():
        generate_json_report(date.today() - timedelta(days=1))

# 任務2: 每月1號存月報表 (HTML)
@scheduler.task('cron', id='monthly_job', day='1', hour='0', minute='10')
def auto_save_monthly_report():
    with app.app_context():
        # 取得「上個月」的年月份
        today = date.today()
        first_of_this_month = today.replace(day=1)
        last_month_date = first_of_this_month - timedelta(days=1)
        
        generate_monthly_html_report(last_month_date.year, last_month_date.month)
        print(f"Monthly report for {last_month_date.strftime('%Y-%m')} generated.")

# --- 路由 ---
# (dashboard, add_product, sell... 保持不變)
@app.route('/')
def dashboard():
    if not session.get('logged_in'): return redirect(url_for('login'))
    categories = Category.query.all()
    return render_template('dashboard.html', categories=categories)

@app.route('/api/add_category', methods=['POST'])
def api_add_category():
    data = request.get_json()
    new_name = data.get('name', '').strip()
    if not new_name: return jsonify({'success': False, 'message': '名稱為空'})
    if Category.query.filter_by(name=new_name).first(): return jsonify({'success': False, 'message': '分類已存在'})
    new_cat = Category(name=new_name)
    db.session.add(new_cat)
    db.session.commit()
    return jsonify({'success': True, 'id': new_cat.id, 'name': new_cat.name})

@app.route('/add_product', methods=['POST'])
def add_product():
    # ... (保持原有的 add_product 代碼) ...
    name = request.form['name'].strip()
    stock_input = request.form.get('stock')
    new_stock = int(stock_input) if stock_input else 0
    cost_input = request.form.get('cost')
    price_input = request.form.get('price')
    category_id = request.form.get('category_id')
    file = request.files.get('image')

    existing_prod = Product.query.filter_by(name=name).first()

    if existing_prod:
        if existing_prod.is_deleted:
            existing_prod.is_deleted = False
            flash(f"商品「{name}」已從刪除列表中恢復！", "success")
        existing_prod.stock += new_stock
        if cost_input: existing_prod.cost = float(cost_input)
        if price_input: existing_prod.price = float(price_input)
        if category_id: existing_prod.category_id = int(category_id)
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            existing_prod.image = filename
        db.session.commit()
        if not existing_prod.is_deleted:
            flash(f"商品「{name}」已進貨，庫存增加：{new_stock}")
    else:
        if not cost_input or not price_input:
            flash("錯誤：新商品必須輸入成本與售價！")
            return redirect(url_for('dashboard'))
        filename = secure_filename(file.filename) if file and file.filename != '' else "default.jpg"
        if file and file.filename != '':
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        cat_id = int(category_id) if category_id else 1
        new_prod = Product(name=name, cost=float(cost_input), price=float(price_input), image=filename, stock=new_stock, category_id=cat_id, is_deleted=False)
        db.session.add(new_prod)
        db.session.commit()
        flash(f"成功新增全新商品：{name}")
    return redirect(url_for('dashboard'))

@app.route('/delete_product/<int:prod_id>', methods=['POST'])
def delete_product(prod_id):
    # ... (保持原有的 delete_product 代碼) ...
    prod = Product.query.get_or_404(prod_id)
    sales_count = Sale.query.filter_by(product_id=prod.id).count()
    if sales_count == 0:
        db.session.delete(prod)
        db.session.commit()
        flash(f"商品「{prod.name}」無銷售紀錄，已永久刪除。")
    else:
        prod.is_deleted = True
        prod.stock = 0 
        db.session.commit()
        flash(f"商品「{prod.name}」包含歷史帳務，已移至隱藏列表 (軟刪除)。")
    return redirect(url_for('dashboard'))

@app.route('/sell/<int:prod_id>', methods=['POST'])
def sell(prod_id):
    # ... (保持原有的 sell 代碼) ...
    prod = Product.query.get_or_404(prod_id)
    qty = int(request.form['quantity'])
    if prod.stock <= 0 or qty > prod.stock:
        flash("已無庫存")
        return redirect(url_for('dashboard'))
    prod.stock -= qty
    profit = (prod.price - prod.cost) * qty
    revenue = prod.price * qty
    db.session.add(Sale(product_id=prod.id, quantity=qty, profit=profit, revenue=revenue))
    db.session.commit()
    flash(f"售出 {qty} 件 {prod.name}")
    return redirect(url_for('dashboard'))

# [新功能] 手動導出月報表 (測試用)
@app.route('/manual_monthly_export')
def manual_monthly_export():
    if not session.get('logged_in'): return redirect(url_for('login'))
    # 預設導出「本月」的，方便你現在立刻看到效果
    today = date.today()
    filename = generate_monthly_html_report(today.year, today.month)
    flash(f"月報表已保存 請查看 (檔案：{filename})")
    return redirect(url_for('reports'))

@app.route('/manual_export')
def manual_export():
    if not session.get('logged_in'): return redirect(url_for('login'))
    filename = generate_json_report(date.today())
    flash(f"今日數據已導出至 {filename}")
    return redirect(url_for('reports'))

@app.route('/reports')
def reports():
    if not session.get('logged_in'): return redirect(url_for('login'))
    
    # ================= [新增] 掉單補救機制開始 =================
    # 邏輯：檢查「上個月」的報表檔案是否存在，如果不在就補做
    today = date.today()
    # 取得本月1號
    first_of_this_month = today.replace(day=1)
    # 倒退一天，取得上個月的最後一天 (例如 2026-01-31)
    last_month_end = first_of_this_month - timedelta(days=1)
    # 取得上個月的年、月 (例如 2026, 1)
    target_year = last_month_end.year
    target_month = last_month_end.month
    
    # 拼湊出應該要有的檔名
    expected_filename = f"monthly_{target_year}_{target_month:02d}.html"
    expected_path = os.path.join(app.config['RECORDS_FOLDER'], expected_filename)
    
    # 檢查檔案是否存在
    if not os.path.exists(expected_path):
        # 如果不存在，代表那天可能沒開機，現在立刻補做！
        generate_monthly_html_report(target_year, target_month)
        flash(f"系統偵測到「{target_month}月」報表尚未建立（可能因當時電腦未開機），已自動為您補齊！", "success")
    # ================= [新增] 掉單補救機制結束 =================
    # ... (保持原有的 reports 代碼) ...
    if not session.get('logged_in'): return redirect(url_for('login'))
    two_days_ago = datetime.now() - timedelta(days=2)
    recent_sales = Sale.query.filter(Sale.timestamp >= two_days_ago).order_by(Sale.timestamp.desc()).all()
    
    daily_stats = db.session.query(
        func.date(Sale.timestamp).label('d'), 
        func.sum(Sale.profit).label('total_profit'),
        func.sum(Sale.quantity).label('total_qty'),
        func.sum(Sale.revenue).label('total_revenue')
    ).group_by('d').order_by(func.date(Sale.timestamp).desc()).limit(7).all()
    daily_stats = daily_stats[::-1]

    d_labels = [str(row.d) for row in daily_stats]
    d_profit = [float(row.total_profit or 0) for row in daily_stats]
    d_qty = [int(row.total_qty or 0) for row in daily_stats]
    d_revenue = [float(row.total_revenue or 0) for row in daily_stats]

    monthly_stats = db.session.query(
        func.strftime('%Y-%m', Sale.timestamp).label('m'), 
        func.sum(Sale.profit).label('total_profit'),
        func.sum(Sale.quantity).label('total_qty'),
        func.sum(Sale.revenue).label('total_revenue')
    ).group_by('m').order_by('m').all()

    m_labels = [str(row.m) for row in monthly_stats]
    m_profit = [float(row.total_profit or 0) for row in monthly_stats]
    m_qty = [int(row.total_qty or 0) for row in monthly_stats]
    m_revenue = [float(row.total_revenue or 0) for row in monthly_stats]

    today = date.today()
    first_day_of_month = datetime(today.year, today.month, 1)
    product_stats = db.session.query(
        Product.name,
        func.sum(Sale.profit).label('total_profit'),
        func.sum(Sale.quantity).label('total_qty'),
        func.sum(Sale.revenue).label('total_revenue')
    ).join(Sale).filter(Sale.timestamp >= first_day_of_month).group_by(Product.name).all()

    p_labels = [row.name for row in product_stats]
    p_profit = [float(row.total_profit or 0) for row in product_stats]
    p_qty = [int(row.total_qty or 0) for row in product_stats]
    p_revenue = [float(row.total_revenue or 0) for row in product_stats]
    
    history_files = []
    if os.path.exists(app.config['RECORDS_FOLDER']):
        history_files = sorted(os.listdir(app.config['RECORDS_FOLDER']), reverse=True)

    return render_template('reports.html', 
                           recent_sales=recent_sales, 
                           history_files=history_files,
                           chart_data={
                               "daily": {"labels": d_labels, "profit": d_profit, "qty": d_qty, "revenue": d_revenue},
                               "monthly": {"labels": m_labels, "profit": m_profit, "qty": m_qty, "revenue": m_revenue},
                               "product": {"labels": p_labels, "profit": p_profit, "qty": p_qty, "revenue": p_revenue}
                           })

# [修改] 檢視報表功能：兼容 JSON 和 HTML
@app.route('/view_report/<filename>')
def view_report(filename):
    if not session.get('logged_in'): return redirect(url_for('login'))
    path = os.path.join(app.config['RECORDS_FOLDER'], filename)
    
    if os.path.exists(path):
        # 如果是 HTML 檔案，直接傳送檔案 (瀏覽器會直接打開)
        if filename.endswith('.html'):
            return send_from_directory(app.config['RECORDS_FOLDER'], filename)
        
        # 如果是 JSON 檔案，走原本的邏輯
        elif filename.endswith('.json'):
            with open(path, 'r', encoding='utf-8') as f: 
                return render_template('report_detail.html', data=json.load(f))
                
    return "File not found", 404

# ... (login, logout, main 保持不變) ...
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if User.query.filter_by(username=request.form['username'], password=request.form['password']).first():
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        flash("錯誤")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

#TEST
@app.route('/debug/simulate_month_end')
def debug_simulate_month_end():
    if not session.get('logged_in'): return redirect(url_for('login'))
    
    # 取得「今天」所在的年、月
    today = date.today()
    
    # 強制執行生成報表邏輯
    # 這會把 "這個月 1號" 到 "今天這一刻" 的所有資料，視為一個完整的月報表
    filename = generate_monthly_html_report(today.year, today.month)
    
    flash(f"🔴【測試成功】已強制模擬本月結算！報表已生成：{filename}")
    return redirect(url_for('reports'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.first(): db.session.add(User(username='admin', password='123')) 
        if not Category.query.first(): db.session.add(Category(name='一般商品'))
        db.session.commit()
    scheduler.init_app(app)
    scheduler.start()
    app.run(debug=True)