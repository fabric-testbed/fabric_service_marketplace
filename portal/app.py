import json

import click
from flask import Flask, render_template, session
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from models import db, User

csrf = CSRFProtect()


def _upgrade_database():
    """db.create_all() never alters existing tables; add new Service columns by hand,
    and switch services from the removed API-key mode to the FABRIC token mode."""
    existing = {c['name'] for c in db.inspect(db.engine).get_columns('services')}
    new_columns = {
        'auth_mode': "VARCHAR NOT NULL DEFAULT 'fabric_token'",
        'link_url': 'VARCHAR',
    }
    with db.engine.begin() as conn:
        for name, ddl in new_columns.items():
            if name not in existing:
                conn.execute(db.text(f'ALTER TABLE services ADD COLUMN {name} {ddl}'))
        conn.execute(db.text("UPDATE services SET auth_mode = 'fabric_token' "
                             "WHERE auth_mode NOT IN ('fabric_token', 'link')"))


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    if app.config['BEHIND_PROXY']:
        # Nginx sets X-Forwarded-For / X-Forwarded-Proto (see nginx/portal.conf)
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_app(app)
    csrf.init_app(app)

    # Register blueprints
    from routes.auth import auth_bp
    from routes.registration import registration_bp
    from routes.services import services_bp
    from routes.my_services import my_services_bp
    from routes.proxy import proxy_bp
    from routes.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(registration_bp)
    app.register_blueprint(services_bp)
    app.register_blueprint(my_services_bp)
    app.register_blueprint(proxy_bp)
    app.register_blueprint(admin_bp)

    # Landing page
    @app.route('/')
    def landing():
        return render_template('landing.html')

    # Jinja2 filter to parse JSON strings in templates
    @app.template_filter('from_json')
    def from_json_filter(s):
        try:
            return json.loads(s)
        except (TypeError, ValueError):
            return []

    # Make session info available to all templates
    @app.context_processor
    def inject_user():
        return {'current_user': session.get('portal_user')}

    # Create tables, and add columns introduced after a database was created
    with app.app_context():
        db.create_all()
        _upgrade_database()

    # CLI command to seed admin
    @app.cli.command('seed-admin')
    @click.argument('fabric_uuid')
    def seed_admin(fabric_uuid):
        """Create or promote a user to admin by FABRIC UUID."""
        user = User.query.filter_by(fabric_uuid=fabric_uuid).first()
        if user:
            user.role = 'admin'
            print(f"Promoted existing user '{user.username}' to admin.")
        else:
            user = User(
                fabric_uuid=fabric_uuid,
                username='admin',
                role='admin'
            )
            db.session.add(user)
            print(f"Created admin user with UUID: {fabric_uuid}")
        db.session.commit()

    # CLI command to verify the portal -> gateway connection
    @app.cli.command('check-gateway')
    def check_gateway():
        """Check gateway URL, TLS cert, IP allowlist and shared secret."""
        import gateway_client
        try:
            ok, detail = gateway_client.health()
        except gateway_client.GatewayError as e:
            ok, detail = False, str(e)
        print(('OK: ' if ok else 'FAILED: ') + str(detail))
        if not ok:
            raise SystemExit(1)

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
