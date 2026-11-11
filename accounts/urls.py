from django.urls import path

from . import views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("trocar-senha/", views.trocar_senha_view, name="trocar_senha"),
    path("meu-perfil/", views.meu_perfil_view, name="meu_perfil"),
    path("alternar-tema/", views.alternar_tema_view, name="alternar_tema"),
    path("alternar-favorito/", views.alternar_favorito_view, name="alternar_favorito"),

    # Usuários (tela própria, fora do /admin/)
    path("usuarios/", views.usuario_lista, name="usuario_lista"),
    path("usuarios/novo/", views.usuario_novo, name="usuario_novo"),
    path("usuarios/<int:pk>/editar/", views.usuario_editar, name="usuario_editar"),
    path("usuarios/<int:pk>/status/", views.usuario_alternar_status, name="usuario_alternar_status"),
    path("usuarios/<int:pk>/redefinir-senha/", views.usuario_redefinir_senha, name="usuario_redefinir_senha"),
    path("usuarios/<int:pk>/excluir/", views.usuario_excluir, name="usuario_excluir"),

    # Perfis (Groups) — permissões por departamento
    path("perfis/", views.perfil_lista, name="perfil_lista"),
    path("perfis/novo/", views.perfil_novo, name="perfil_novo"),
    path("perfis/<int:pk>/editar/", views.perfil_editar, name="perfil_editar"),
    path("perfis/<int:pk>/excluir/", views.perfil_excluir, name="perfil_excluir"),

    # Log de auditoria — consulta (gravação é automática, ver LogAuditoria.registrar)
    path("log-auditoria/", views.log_auditoria_lista, name="log_auditoria_lista"),
]
