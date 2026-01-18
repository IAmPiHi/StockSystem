import os, json
from datetime import datetime, timedelta, date
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
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

# --- Models ---

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
    profit = db.Column(db.Float, nullable=False)  # 淨利潤 (價差 * 數量)
    revenue = db.Column(db.Float, default=0.0)    # [新] 總營收 (售價 * 數量)
    timestamp = db.Column(db.DateTime, default=datetime.now)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)

# --- 報表邏輯 ---
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
        # 兼容舊資料 (如果 revenue 是 None)
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
        
        if s.product.name not in item_summary: 
            item_summary[s.product.name] = {"qty": 0, "profit": 0, "revenue": 0}
        item_summary[s.product.name]["qty"] += s.quantity
        item_summary[s.product.name]["profit"] += s.profit
        item_summary[s.product.name]["revenue"] += rev

    report_data = {
        "date": target_date.strftime("%Y-%m-%d"),
        "summary": {
            "total_profit": round(total_profit, 2),
            "total_revenue": round(total_revenue, 2),
            "total_sales_count": sum(hourly_data)
        },
        "hourly_chart": hourly_data,
        "item_summary": item_summary,
        "raw_sales": detail_list
    }
    
    filename = f"report_{target_date.strftime('%Y%m%d')}.json"
    with open(os.path.join(app.config['RECORDS_FOLDER'], filename), 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=4)
    return filename

@scheduler.task('cron', id='do_job_1', day='*', hour='0', minute='0')
def auto_save_report():
    with app.app_context():
        generate_json_report(date.today() - timedelta(days=1))

# --- 路由邏輯 ---

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

        new_prod = Product(
            name=name, 
            cost=float(cost_input), 
            price=float(price_input), 
            image=filename, 
            stock=new_stock,
            category_id=cat_id,
            is_deleted=False
        )
        db.session.add(new_prod)
        db.session.commit()
        flash(f"成功新增全新商品：{name}")

    return redirect(url_for('dashboard'))

@app.route('/delete_product/<int:prod_id>', methods=['POST'])
def delete_product(prod_id):
    prod = Product.query.get_or_404(prod_id)
    # 檢查是否有銷售紀錄
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
    prod = Product.query.get_or_404(prod_id)
    qty = int(request.form['quantity'])
    if prod.stock <= 0 or qty > prod.stock:
        flash("已無庫存")
        return redirect(url_for('dashboard'))
    
    prod.stock -= qty
    profit = (prod.price - prod.cost) * qty
    revenue = prod.price * qty  # [新] 計算總營收
    
    db.session.add(Sale(product_id=prod.id, quantity=qty, profit=profit, revenue=revenue))
    db.session.commit()
    flash(f"售出 {qty} 件 {prod.name}")
    return redirect(url_for('dashboard'))

@app.route('/manual_export')
def manual_export():
    if not session.get('logged_in'): return redirect(url_for('login'))
    filename = generate_json_report(date.today())
    flash(f"今日數據已導出至 {filename}")
    return redirect(url_for('reports'))

@app.route('/reports')
def reports():
    if not session.get('logged_in'): return redirect(url_for('login'))
    
    # --- 1. 抓取近期銷售 ---
    two_days_ago = datetime.now() - timedelta(days=2)
    recent_sales = Sale.query.filter(Sale.timestamp >= two_days_ago).order_by(Sale.timestamp.desc()).all()
    
    # --- 2. 每日統計 (利潤、數量、營收) ---
    daily_stats = db.session.query(
        func.date(Sale.timestamp).label('d'), 
        func.sum(Sale.profit).label('total_profit'),
        func.sum(Sale.quantity).label('total_qty'),
        func.sum(Sale.revenue).label('total_revenue') # [新]
    ).group_by('d').order_by(func.date(Sale.timestamp).desc()).limit(7).all()
    
    daily_stats = daily_stats[::-1]

    d_labels = [str(row.d) for row in daily_stats]
    d_profit = [float(row.total_profit or 0) for row in daily_stats]
    d_qty = [int(row.total_qty or 0) for row in daily_stats]
    d_revenue = [float(row.total_revenue or 0) for row in daily_stats] # [新]
    
    # --- 3. 每月統計 (利潤、數量、營收) ---
    monthly_stats = db.session.query(
        func.strftime('%Y-%m', Sale.timestamp).label('m'), 
        func.sum(Sale.profit).label('total_profit'),
        func.sum(Sale.quantity).label('total_qty'),
        func.sum(Sale.revenue).label('total_revenue') # [新]
    ).group_by('m').order_by('m').all()

    m_labels = [str(row.m) for row in monthly_stats]
    m_profit = [float(row.total_profit or 0) for row in monthly_stats]
    m_qty = [int(row.total_qty or 0) for row in monthly_stats]
    m_revenue = [float(row.total_revenue or 0) for row in monthly_stats] # [新]
# --- 4. 每月占比 ---
    today = date.today()
    first_day_of_month = datetime(today.year, today.month, 1)

    product_stats = db.session.query(
        Product.name,
        func.sum(Sale.profit).label('total_profit'),
        func.sum(Sale.quantity).label('total_qty'),
        func.sum(Sale.revenue).label('total_revenue')
    ).join(Sale).filter(
        Sale.timestamp >= first_day_of_month  # <--- 關鍵：只選大於等於本月1號的資料
    ).group_by(Product.name).all()

    p_labels = [row.name for row in product_stats]
    p_profit = [float(row.total_profit or 0) for row in product_stats]
    p_qty = [int(row.total_qty or 0) for row in product_stats]
    p_revenue = [float(row.total_revenue or 0) for row in product_stats]

    # --- 5. 歷史報表檔案列表 ---
    
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

# (其他路由保持不變...)
@app.route('/view_report/<filename>')
def view_report(filename):
    if not session.get('logged_in'): return redirect(url_for('login'))
    path = os.path.join(app.config['RECORDS_FOLDER'], filename)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f: return render_template('report_detail.html', data=json.load(f))
    return "File not found", 404

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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.first(): db.session.add(User(username='admin', password='123')) 
        if not Category.query.first(): db.session.add(Category(name='一般商品'))
        db.session.commit()
    scheduler.init_app(app)
    scheduler.start()
    app.run(debug=True)