from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_admin.menu import MenuLink
from flask import redirect, url_for, session
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()


# --- Custom Admin Views ------------------------------------------------------

class SecureAdminIndexView(AdminIndexView):
    def is_accessible(self):
        return "user" in session and session.get("user_role") == "admin"

    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for("login"))


class SecureModelView(ModelView):
    def is_accessible(self):
        return "user" in session and session.get("user_role") == "admin"

    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for("login"))


class ReadOnlyView(SecureModelView):
    can_create = False
    can_edit = False
    can_delete = False


# ✅ Custom User Admin View (only this should be used for User model)
class UserAdminView(SecureModelView):
    column_exclude_list = ["password"]
    form_columns = ["username", "password", "role"]

    # Correct way to define dropdown choices
    form_choices = {
        'role': [
            ('user', 'User'),
            ('admin', 'Admin')
        ]
    }

    def on_model_change(self, form, model, is_created):
        print(">>> USING UserAdminView <<<")  # debug confirmation
        if form.password.data and not form.password.data.startswith("$2b$"):
            model.password = bcrypt.generate_password_hash(form.password.data).decode("utf-8")


# --- Admin Initialization ----------------------------------------------------

def init_admin(app, db, User, Tariff, ContainerInfo, Report, url_prefix="/admin_panel"):

    admin = Admin(
        app,
        name="Alliance Admin Panel",
        index_view=SecureAdminIndexView(url=url_prefix),
        url=url_prefix,
        template_mode="bootstrap3"
    )

    # Add views
    admin.add_view(SecureModelView(Tariff, db.session, category="Data Management"))
    admin.add_view(ReadOnlyView(Report, db.session, category="Reports"))
    admin.add_view(SecureModelView(ContainerInfo, db.session, category="Container Info"))
    admin.add_view(UserAdminView(User, db.session, category="User Management"))  # ✅ Only this for users

    admin.add_link(MenuLink(name="Back to Portal", category="", url="/dashboard"))
    admin.add_link(MenuLink(name="Logout", category="", url="/logout"))

    print("✅ Flask-Admin initialized with:")
    for v in admin._views:
        print("   ", v.__class__.__name__, "→", getattr(v, "model", None))

    return admin
