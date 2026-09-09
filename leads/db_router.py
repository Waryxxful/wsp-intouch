class LeadsRouter:
    def db_for_read(self, model, **hints):
        return "qaintouch" if model._meta.app_label == "leads" else None

    def db_for_write(self, model, **hints):
        return "qaintouch" if model._meta.app_label == "leads" else None

    def allow_migrate(self, db, app_label, **hints):
        if app_label == "leads":
            return db == "qaintouch"
        if db == "qaintouch":
            return False
        return None
