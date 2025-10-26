from flask import Flask, render_template, request, redirect, url_for, send_file, flash, session
from flask_talisman import Talisman
import os, zipfile, stripe
from io import BytesIO
from datetime import datetime
from PIL import Image
from dotenv import load_dotenv

# ================================
# CONFIGURACIÓN BASE
# ================================
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "clave_segura_local")

# --- Parches de seguridad ---
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    MAX_CONTENT_LENGTH=50 * 1024 * 1024  # límite 50MB global
)

# HTTPS y cabeceras de seguridad
#Talisman(app, content_security_policy=None)
csp = {
    'default-src': [
        "'self'",
        'https://connect.facebook.net',
        'https://www.facebook.com'
    ],
    'script-src': [
        "'self'",
        "'unsafe-inline'",
        "'unsafe-eval'",
        'https://connect.facebook.net',
        'https://www.facebook.com'
    ],
    'frame-src': [
        "'self'",
        'https://www.facebook.com',
        'https://connect.facebook.net'
    ],
    'style-src': [
        "'self'",
        "'unsafe-inline'"
    ],
    'img-src': [
        "'self'",
        'data:',
        'https://www.facebook.com'
    ]
}

Talisman(app, content_security_policy=csp)

# ================================
# CONFIG STRIPE
# ================================
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

FREE_LIMIT_PER_DAY = 2
FREE_SIZE_LIMIT = 2 * 1024 * 1024
PREMIUM_SIZE_LIMIT = 50 * 1024 * 1024


# ================================
# FUNCIONES AUXILIARES
# ================================
def check_free_limit():
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"uploads_{today}"
    if key not in session:
        session[key] = 0
    return session[key] < FREE_LIMIT_PER_DAY


def increment_upload_count():
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"uploads_{today}"
    session[key] = session.get(key, 0) + 1


def allowed_file(filename):
    ALLOWED = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'zip'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED


# ================================
# RUTAS
# ================================
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/donate")
def donate():
    return render_template("donate.html")


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': 'Donación al creador del CV Virtual'},
                    'unit_amount': 500,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('success_donation', _external=True),
            cancel_url=url_for('donate', _external=True),
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return str(e)


@app.route("/success-donation")
def success_donation():
    return render_template("success_donation.html")


@app.route("/compressor", methods=["GET", "POST"])
def compressor():
    today = datetime.now().strftime("%Y-%m-%d")
    used = session.get(f"uploads_{today}", 0)
    remaining = max(0, FREE_LIMIT_PER_DAY - used)
    is_premium = session.get("premium", False)

    if request.method == "POST":
        # Obtener todos los archivos seleccionados
        uploaded_files = request.files.getlist("files")
        format_option = request.form.get("format")

        if not uploaded_files or uploaded_files == [None]:
            flash("Por favor selecciona al menos un archivo.")
            return redirect(request.url)

        # Calcular tamaño total
        total_size = 0
        for file in uploaded_files:
            file.seek(0, os.SEEK_END)
            total_size += file.tell()
            file.seek(0)

        size_limit = PREMIUM_SIZE_LIMIT if is_premium else FREE_SIZE_LIMIT
        if total_size > size_limit:
            flash(f"El tamaño total de los archivos ({total_size / (1024 * 1024):.2f} MB) "
                  f"supera el límite de {size_limit / (1024 * 1024)} MB.")
            return redirect(request.url)

        # Verificar límite diario gratuito
        if not is_premium and not check_free_limit():
            flash("Límite gratuito alcanzado. ¡Actualiza a Premium!")
            return redirect(url_for("premium"))

        # Crear ZIP en memoria
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file in uploaded_files:
                if not allowed_file(file.filename):
                    flash(f"Archivo no permitido: {file.filename}")
                    continue

                file_path = os.path.join(UPLOAD_FOLDER, file.filename)
                file.save(file_path)

                # Conversión opcional de formato (solo imágenes)
                if format_option and format_option != "none":
                    try:
                        image = Image.open(file_path)
                        new_path = os.path.splitext(file_path)[0] + f".{format_option}"
                        image.save(new_path, format_option.upper())
                        os.remove(file_path)
                        file_path = new_path
                    except Exception:
                        flash(f"No se pudo convertir: {file.filename}")

                # Agregar archivo al ZIP
                zipf.write(file_path, arcname=os.path.basename(file_path))
                os.remove(file_path)

        zip_buffer.seek(0)

        # Incrementar contador diario gratuito
        if not is_premium:
            increment_upload_count()

        # Descargar ZIP
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name="archivos_comprimidos.zip",
            mimetype="application/zip"
        )

    # Renderizar vista
    return render_template(
        "compressor.html",
        remaining=remaining, used=used,
        limit=FREE_LIMIT_PER_DAY, is_premium=is_premium
    )


    return render_template("compressor.html", remaining=remaining, used=used, limit=FREE_LIMIT_PER_DAY, is_premium=is_premium)


@app.route("/premium")
def premium():
    return render_template("premium.html")


@app.route("/create-premium-session", methods=["POST"])
def create_premium_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': 'Compresor Premium'},
                    'unit_amount': 900,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('success_premium', _external=True),
            cancel_url=url_for('premium', _external=True),
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return str(e)


@app.route("/success-premium")
def success_premium():
    session["premium"] = True
    return render_template("success_premium.html")

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy-policy.html')

@app.route('/data-deletion')
def data_deletion():
    return render_template('data-deletion.html')

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
