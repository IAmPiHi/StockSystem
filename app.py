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
    # 這裡的 products 關聯保持不變
    products = db.relationship('Product', backref='category', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    image = db.Column(db.String(100))
    cost = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    
    # [新功能] 軟刪除標記：True 代表已刪除(隱藏)，False 代表正常顯示
    is_deleted = db.Column(db.Boolean, default=False)
    
    sales = db.relationship('Sale', backref='product', lazy=True)

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    profit = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)

# --- 報表邏輯 (保持不變) ---
def generate_json_report(target_date):
    start_of_day = datetime.combine(target_date, datetime.min.time())
    end_of_day = datetime.combine(target_date, datetime.max.time())
    sales_today = Sale.query.filter(Sale.timestamp >= start_of_day, Sale.timestamp <= end_of_day).all()
    
    hourly_data = [0] * 24
    for s in sales_today: hourly_data[s.timestamp.hour] += s.quantity
    
    detail_list = []
    item_summary = {}
    total_profit = 0
    
    for s in sales_today:
        detail_list.append({
            "time": s.timestamp.strftime("%H:%M:%S"),
            "product": s.product.name,
            "qty": s.quantity,
            "profit": round(s.profit, 2)
        })
        total_profit += s.profit
        if s.product.name not in item_summary: item_summary[s.product.name] = {"qty": 0, "profit": 0}
        item_summary[s.product.name]["qty"] += s.quantity
        item_summary[s.product.name]["profit"] += s.profit

    report_data = {
        "date": target_date.strftime("%Y-%m-%d"),
        "summary": {"total_profit": round(total_profit, 2), "total_sales_count": sum(hourly_data)},
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
    
    # 這裡不需要改，因為我們會在 template 裡面用 if 判斷
    # 或者要在後端過濾也可以，但在這裡傳所有分類比較方便
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

# [重點修改] 新增/復活商品邏輯
@app.route('/add_product', methods=['POST'])
def add_product():
    name = request.form['name'].strip()
    stock_input = request.form.get('stock')
    new_stock = int(stock_input) if stock_input else 0
    cost_input = request.form.get('cost')
    price_input = request.form.get('price')
    category_id = request.form.get('category_id')
    file = request.files.get('image')

    # 搜尋是否已經有這個商品（不管是顯示中還是已刪除）
    existing_prod = Product.query.filter_by(name=name).first()

    if existing_prod:
        # --- 情況 A：商品已存在 (無論是否被刪除) ---
        
        # 1. 如果它是「已刪除」狀態，我們將它復活
        if existing_prod.is_deleted:
            existing_prod.is_deleted = False
            flash(f"商品「{name}」已從刪除列表中恢復！", "success")
            # 復活時，我們通常視為重新上架，可以選擇是否保留舊庫存
            # 根據你的需求：如果是重新初始化，我們直接加上新庫存
        
        # 2. 更新庫存 (累加)
        existing_prod.stock += new_stock
        
        # 3. 更新價格與成本 (如果有填寫)
        if cost_input: existing_prod.cost = float(cost_input)
        if price_input: existing_prod.price = float(price_input)
        if category_id: existing_prod.category_id = int(category_id)
        
        # 4. 更新圖片
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            existing_prod.image = filename
            
        db.session.commit()
        if not existing_prod.is_deleted:
            flash(f"商品「{name}」已進貨，庫存增加：{new_stock}")
            
    else:
        # --- 情況 B：全新商品 ---
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
            is_deleted=False # 預設顯示
        )
        db.session.add(new_prod)
        db.session.commit()
        flash(f"成功新增全新商品：{name}")

    return redirect(url_for('dashboard'))

# [重點修改] 刪除變為「軟刪除」
@app.route('/delete_product/<int:prod_id>', methods=['POST'])
def delete_product(prod_id):
    prod = Product.query.get_or_404(prod_id)
    
    # 這次我們不檢查銷售紀錄了，因為我們只是隱藏它，不會破壞資料庫
    # 執行軟刪除
    prod.is_deleted = True
    prod.stock = 0 # 隱藏時將庫存歸零，避免影響總資產計算，下次進貨再重新加
    
    db.session.commit()
    flash(f"商品「{prod.name}」已刪除。若需恢復，可以直接重新進貨該商品名稱。")
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
    db.session.add(Sale(product_id=prod.id, quantity=qty, profit=profit))
    db.session.commit()
    flash(f"售出 {qty} 件 {prod.name}")
    return redirect(url_for('dashboard'))

# --- 其他路由 (Reports, Login, Init) ---
@app.route('/manual_export')
def manual_export():
    if not session.get('logged_in'): return redirect(url_for('login'))
    filename = generate_json_report(date.today())
    flash(f"今日數據已導出至 {filename}")
    return redirect(url_for('reports'))

@app.route('/reports')
def reports():
    if not session.get('logged_in'): return redirect(url_for('login'))
    
    # --- 1. 抓取近期銷售 (保持不變) ---
    two_days_ago = datetime.now() - timedelta(days=2)
    recent_sales = Sale.query.filter(Sale.timestamp >= two_days_ago).order_by(Sale.timestamp.desc()).all()
    
    # --- 2. 每日利潤統計 (修改取值方式) ---
    # 查詢結果 index 0 是日期, index 1 是總利潤
    daily_stats = db.session.query(
        func.date(Sale.timestamp).label('d'), 
        func.sum(Sale.profit).label('t')
    ).group_by('d').limit(7).all()
    
    d_labels = []
    d_values = []
    for row in daily_stats:
        # row[0] 是日期，row[1] 是金額
        d_labels.append(str(row[0]))          
        d_values.append(float(row[1] or 0))   
    
    # --- 3. 每月利潤統計 (修改取值方式) ---
    # 查詢結果 index 0 是月份, index 1 是總利潤
    monthly_stats = db.session.query(
        func.strftime('%Y-%m', Sale.timestamp).label('m'), 
        func.sum(Sale.profit).label('t')
    ).group_by('m').order_by('m').all()

    m_labels = []
    m_values = []
    for row in monthly_stats:
        # row[0] 是月份，row[1] 是金額
        m_labels.append(str(row[0]))          
        m_values.append(float(row[1] or 0))   
    
    # --- 4. 歷史檔案列表 ---
    history_files = []
    if os.path.exists(app.config['RECORDS_FOLDER']):
        history_files = sorted(os.listdir(app.config['RECORDS_FOLDER']), reverse=True)

    return render_template('reports.html', 
                           recent_sales=recent_sales, 
                           daily_labels=d_labels, 
                           daily_values=d_values, 
                           monthly_labels=m_labels, 
                           monthly_values=m_values,
                           history_files=history_files)

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
        if not User.query.first(): db.session.add(User(username='admin', password='123'))  #modify it yourself
        if not Category.query.first(): db.session.add(Category(name='一般商品'))
        db.session.commit()
    scheduler.init_app(app)
    scheduler.start()
    app.run(debug=True)