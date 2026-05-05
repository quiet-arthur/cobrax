import os
from dotenv import load_dotenv

load_dotenv()

# ====== ALMAH URLS ====== 

ENDPOINTS = {
    "login": "/SIS/EmpresaWS.asmx/SelecionarPorLoginSenha",
    "access": "/novo_acesso.aspx",
    "units_export": "/CND/UnidadeCondominioWS.asmx/Exportar",
    "units_datatable": "/CND/CND00701.aspx?jqDados=S&draw=1&start=0&length=5000&search=&order=",
    "units_bils_export": "/FIN/ContasAReceberWS.asmx/GerarRelatorioInadimplenciaCondominioXLS",
}

# ======================

# ====== ADMS CONFIG =====

ADMS_CONFIG = {
    "alpha": {
        "url": "https://alphaassessoriams.almahcondos.com.br",
        "ids_ambiente": {
            "id_empresa": 154,
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
            "id_empresa": 143,
            "id_usuario": 6,
            "id_estabelecimento": 1,
            "id_perfil_de_uso": 1,
        },
        "user_login": "LOGIN_USER",
        "user_password": "EXPRESSO_PASSWORD_HASH"
    }
}

# ======================

# ====== CREDENTIAL HELPERS ======

def get_adm_credentials(adm: str) -> dict[str, str]:
    """
    Resolve as credenciais de uma administradora a partir das variáveis de ambiente.

    Args:
        adm: Chave da administradora (ex: "alpha", "expresso").

    Returns:
        Dict com chaves 'user' e 'password' resolvidas do .env.

    Raises:
        ValueError: Se a administradora não existir em ADMS_CONFIG.
    """
    if adm not in ADMS_CONFIG:
        raise ValueError(f"Administradora '{adm}' não encontrada nas configurações")

    config = ADMS_CONFIG[adm]
    return {
        "user": os.getenv(config["user_login"], ""),
        "password": os.getenv(config["user_password"], ""),
    }

# ======================

# ====== EVOLUTION API ======

EVOLUTION_CONFIG = {
    "base_url": os.getenv("EVOLUTION_API_URL", "http://localhost:8080"),
    "api_key":  os.getenv("EVOLUTION_API_KEY", ""),
    "instance": os.getenv("EVOLUTION_INSTANCE", "wpp_ps"),
}

# ===========================