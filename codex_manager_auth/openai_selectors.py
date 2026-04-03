CSS_OA_SIGNUP_LINK = 'a[href*="create-account"]'
CSS_OA_EMAIL_INPUT = 'input[type="email"][name="email"]'
CSS_OA_CONTINUE_BTN = 'form button[type="submit"][name="intent"]'
CSS_OA_PASSWORD_INPUT = 'input[type="password"][name="new-password"]'
CSS_OA_PASSWORD_BTN = 'form:has(input[name="new-password"]) button[type="submit"]'
CSS_OA_CODE_INPUT = 'input[name="code"]'
CSS_OA_NAME_INPUT = 'input[name="name"]'
CSS_OA_BIRTHDAY_YEAR = '[data-type="year"]'
CSS_OA_BIRTHDAY_HIDDEN_INPUT = 'input[name="birthday"]'
CSS_OA_AGE_INPUT_SELECTORS = (
    'input[name="age"]',
    'input[aria-label="年龄"]',
    'input[placeholder*="年龄"]',
)
CSS_OA_CREATE_ACCOUNT_BTN = 'button[type="submit"]:has-text("完成帐户创建")'
CSS_OA_ACCOUNT_EXISTS_ERROR = 'li:has-text("已存在")'

CSS_L_EMAIL = 'input[type="email"][name="email"]'
CSS_L_CONTINUE_EMAIL = 'form button[type="submit"][name="intent"]'
CSS_L_PASSWORD = 'input[name="current-password"]'
CSS_L_CONTINUE_PWD = 'form:has(input[name="current-password"]) button[type="submit"]'
CSS_L_CODE = 'input[name="code"]'
CSS_L_CONTINUE_CODE = 'button[name="intent"][value="validate"]'
CSS_L_CONSENT_BTN = 'button[type="submit"]:has-text("继续")'
CSS_INVALID_CODE_ERROR = 'li:has-text("代码不正确")'
RATE_LIMIT_MESSAGE_SELECTORS = (
    'text=/Rate limit exceeded/i',
    'text=/try again later/i',
)
RATE_LIMIT_RETRY_BUTTON_SELECTORS = (
    'button:has-text("重试")',
    'button:has-text("Retry")',
)
ADD_PHONE_URL_KEYWORDS = (
    "auth.openai.com/add-phone",
    "/add-phone",
)
