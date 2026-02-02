# ====== ALMAH URLS ====== 

ENDPOINTS = {
    "login": "/SIS/EmpresaWS.asmx/SelecionarPorLoginSenha",
    "access": "/novo_acesso.aspx",
    "units_export": "/CND/UnidadeCondominioWS.asmx/Exportar",
    "units_bils_export": "/FIN/ContasAReceberWS.asmx/GerarRelatorioInadimplenciaCondominioXLS",
}

# ======================

# ====== ADMS CONFIG =====

ADMS_CONFIG = {
    "alpha": {
        "url": "https://alphaassessoriams.almahcondos.com.br",
        "ids_ambiente": {
            "id_usuario": 341,
            "id_estabelecimento": 1,
            "id_perfil_de_uso": 1,
        },
        "user_login": "LOGIN_USER",
        "user_password": "ALPHA_PASSWORD_HASH"
    },

    "expresso": {
        "url": "https://expressoadmapp.almahcondos.com.br",
        "ids_ambiente": {
            "id_usuario": 6,
            "id_estabelecimento": 1,
            "id_perfil_de_uso": 1,
        },
        "user_login": "LOGIN_USER",
        "user_password": "EXPRESSO_PASSWORD_HASH"
    }
}

# ======================