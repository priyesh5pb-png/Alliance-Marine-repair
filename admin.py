import logging
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_admin.menu import MenuLink
from flask import redirect, url_for, session, current_app
from flask_bcrypt import Bcrypt
from wtforms.fields import SelectField

bcrypt = Bcrypt()
logger = logging.getLogger(__name__)  

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

    # columns and ordering used in create/edit forms
    form_columns = ["username", "password", "role"]

    #Force role to be a SelectField (avoid Flask-Admin inferring choices/flags)
    form_overrides = {
        "role" : SelectField
    }

    # Provided the choices explicitly via form_args 
    form_args = {
        'role': [
            "choices" : [("user", "User"), ("admin", "Admin")],
            "coerce" : str
        ]
    }

    def create_form(self, obj=None):
        """
        Wrap create_form to log debug info if WTForms/Flask-Admin tries to pass
        malformed data (Help render debugging)
        """
        try: 
            form = super().create_form(obj=obj)
            #quick debug trace in logs
            logger.debug("UserAdminView.create_form: created form class %s", type(form))
            return form
        except Exception as e:
            #log detailed content in log to inspect in render
            logger.exception("Error creating User form (create_form).obj=%s", repr(obj))
            #re-raise so Flask shows proper 500 and stack trace
            raise

    def on_model_change(self, form, model, is_created):
        """
        Hash plaintext password before saving. If the supplied value already looks
        like a bcrypt hash leave it alone.
        """
        logger.debug("UserAdminView.on_model_change: is_created=%s username=%s", is_created, getattr(model, "username", None))
        if getattr(form, "password", None) and form.password.data:
            pwd = form.password.data
            #If user typed a plaintext password, generate hashed password.
            #If they paste the hashed string, don't double hash
            if not isinstance(pwd, str):
                pwd = str(pwd)
            if not pwd.startswith("$2b$") and not pwd.startswith("$2a$"):
                #bcrypt.generate_password_hash returns bytes in some installs,
                #use decode if needed.
                hashed = bcrypt.generate_password_hash(pwd)
                if isinstance(hashed, bytes):
                    hashed = hashed.decode("utf-8")
                model.password = hashed
            else:
                model.password = pwd

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

    #Use the custom UserAdminView for the model 
    admin.add_view(UserAdminView(User, db.session, category="User Management"))  # ✅ Only this for users

    #links in the admin menu
    admin.add_link(MenuLink(name="Back to Portal", category="", url="/dashboard"))
    admin.add_link(MenuLink(name="Logout", category="", url="/logout"))

    # Log what was initialized
    logger.info("✅ Flask-Admin initialized with views:")
    for v in admin._views:
        logger.info("   %s  -> model=%s", v.__class__.__name__, getattr(v, "model", None))

    return admin
