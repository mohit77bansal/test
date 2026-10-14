from django.contrib import admin

from .models import Role, User, UserRole


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 0
    fk_name = "user"


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["email", "name", "user_type", "status", "last_login_at"]
    list_filter = ["user_type", "status"]
    search_fields = ["email", "name"]
    exclude = ["password", "groups", "user_permissions", "created_by", "updated_by"]
    inlines = [UserRoleInline]


admin.site.register(Role)
