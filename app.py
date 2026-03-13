#app.py
from flask import Flask, render_template
from config import Config

# Blueprints
from routes.otp_routes import otp_bp
from routes.auth_routes import auth_bp
from routes.dashboard_routes import dashboard_bp
from routes.issue_routes import issue_bp
from routes.profile_routes import profile_bp
from routes.admin_routes import admin_bp
from routes.main_routes import main_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Register Blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(otp_bp, url_prefix="/otp")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(dashboard_bp, url_prefix="/dashboard")
    app.register_blueprint(issue_bp, url_prefix="/issues")
    app.register_blueprint(profile_bp, url_prefix="/profile")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # --------------------
    # Error Handlers
    # --------------------
    @app.errorhandler(404)
    def page_not_found(e):
        return render_template("errors/404.html"), 404


    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403


    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500


    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
