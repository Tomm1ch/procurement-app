def user_roles(request):
    user = request.user
    allowed = user.is_authenticated and (user.is_superuser or user.groups.filter(name="Procurement").exists())
    return {"is_procurement": bool(allowed)}
