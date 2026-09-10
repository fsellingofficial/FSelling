function clearRuntimeMessage(element) {
    element.textContent = '';
    delete element.dataset.messageKey;
    delete element.dataset.apiMessage;
}

function showLocalizedMessage(element, key) {
    element.dataset.messageKey = key;
    delete element.dataset.apiMessage;
    element.textContent = t(key);
}

function showApiMessage(element, message) {
    delete element.dataset.messageKey;
    element.dataset.apiMessage = '1';
    element.textContent = message;
}

function setSubmitState(button, key, disabled) {
    button.dataset.i18n = key;
    button.textContent = t(key);
    button.disabled = disabled;
}

function refreshRuntimeMessages() {
    document.querySelectorAll('[data-message-key]').forEach(element => {
        element.textContent = t(element.dataset.messageKey);
    });

    // Lỗi API cũ thuộc ngôn ngữ của lần gửi trước. Xóa nó để không giữ một
    // thông báo sai ngôn ngữ sau khi người dùng chuyển lựa chọn.
    document.querySelectorAll('[data-api-message="1"]').forEach(clearRuntimeMessage);
}

document.addEventListener('fselling:localechange', refreshRuntimeMessages);

document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const openDemoSale = e.currentTarget.dataset.demoSale === '1' && username === 'demo';
    const errorMsg = document.getElementById('errorMsg');
    const submitBtn = e.currentTarget.querySelector('button[type="submit"]');

    clearRuntimeMessage(errorMsg);
    setSubmitState(submitBtn, 'login.submitting', true);

    try {
        const data = await apiCall('/auth/login', 'POST', {
            username,
            password,
            ...buildAuthDeviceMetadata()
        });
        // Same-tab A -> B không phát storage event. Seal A (hoặc ghi marker
        // fail-closed nếu trang login chưa nạp OfflineBan) trước khi overwrite B.
        await prepareAuthIdentityChangeV1(username);
        localStorage.setItem('token', data.access_token);
        localStorage.setItem('role', data.role);
        if (data.role === 'STAFF') {
            localStorage.setItem('staff_role', data.staff_role || 'MANAGER');
        } else {
            localStorage.removeItem('staff_role');
        }
        // Để hóa đơn ở POS ghi được tên người bán. Lấy từ ô đăng nhập chứ không
        // giải mã token: chỉ dùng để hiển thị, không dùng để phân quyền.
        localStorage.setItem('username', username);
        cacheAuthSessionSummary(data.session);
        if (data.role === 'ADMIN') {
            navigateToPage('/admin');
        } else if (openDemoSale && data.role === 'SELLER') {
            navigateToPage('/pos?tour=sale');
        } else if (data.role === 'STAFF' && data.staff_role === 'SERVICE') {
            navigateToPage('/fnb');
        } else if (data.role === 'STAFF' && data.staff_role === 'CASHIER') {
            navigateToPage('/pos');
        } else if (data.role === 'STAFF' && ['KITCHEN', 'BAR'].includes(data.staff_role)) {
            navigateToPage(`/fnb/station/${data.staff_role.toLowerCase()}`);
        } else {
            navigateToPage('/seller');
        }
    } catch (err) {
        setSubmitState(submitBtn, 'login.submit', false);
        if (err.status === 401) {
            showLocalizedMessage(errorMsg, 'login.invalid_credentials');
        } else {
            showApiMessage(errorMsg, err.message);
        }
    }
});

// HÀM QUÊN MẬT KHẨU
function showForgotModal() {
    document.getElementById('forgotModal').style.display = 'flex';
    document.getElementById('forgotStep1Form').style.display = 'block';
    document.getElementById('forgotStep2Form').style.display = 'none';
    clearRuntimeMessage(document.getElementById('forgotErrorMsg'));
    clearRuntimeMessage(document.getElementById('forgotSuccessMsg'));
    document.getElementById('forgotEmail').value = '';
    document.getElementById('forgotOTP').value = '';
    document.getElementById('forgotNewPassword').value = '';
    document.getElementById('forgotConfirmPassword').value = '';
    setSubmitState(
        document.querySelector('#forgotStep1Form button[type="submit"]'),
        'forgot.send_code',
        false
    );
    setSubmitState(
        document.querySelector('#forgotStep2Form button[type="submit"]'),
        'forgot.reset_submit',
        false
    );
    document.getElementById('forgotEmail').focus();
}

function closeForgotModal() {
    document.getElementById('forgotModal').style.display = 'none';
}

document.getElementById('forgotStep1Form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('forgotEmail').value;
    const errorMsg = document.getElementById('forgotErrorMsg');
    const successMsg = document.getElementById('forgotSuccessMsg');
    const submitBtn = e.currentTarget.querySelector('button[type="submit"]');
    clearRuntimeMessage(errorMsg);
    clearRuntimeMessage(successMsg);
    setSubmitState(submitBtn, 'forgot.sending_code', true);
    
    try {
        await apiCall('/auth/forgot-password-request', 'POST', { email });
        setSubmitState(submitBtn, 'forgot.send_code', false);
        showLocalizedMessage(successMsg, 'forgot.code_sent');
        setTimeout(() => {
            document.getElementById('forgotStep1Form').style.display = 'none';
            document.getElementById('forgotStep2Form').style.display = 'block';
            clearRuntimeMessage(successMsg);
            document.getElementById('forgotOTP').focus();
        }, 1500);
    } catch (err) {
        setSubmitState(submitBtn, 'forgot.send_code', false);
        showApiMessage(errorMsg, err.message);
    }
});

document.getElementById('forgotStep2Form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('forgotEmail').value;
    const code = document.getElementById('forgotOTP').value;
    const new_password = document.getElementById('forgotNewPassword').value;
    const confirm = document.getElementById('forgotConfirmPassword').value;
    const errorMsg = document.getElementById('forgotErrorMsg');
    const successMsg = document.getElementById('forgotSuccessMsg');
    const submitBtn = e.currentTarget.querySelector('button[type="submit"]');
    clearRuntimeMessage(errorMsg);
    clearRuntimeMessage(successMsg);
    
    if (new_password !== confirm) {
        showLocalizedMessage(errorMsg, 'auth.validation.password_mismatch');
        return;
    }
    
    const regex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?\":{}|<>_]).+$/;
    if (!regex.test(new_password)) {
        showLocalizedMessage(errorMsg, 'auth.validation.password_policy');
        return;
    }

    setSubmitState(submitBtn, 'forgot.resetting', true);
    
    try {
        await apiCall('/auth/forgot-password-reset', 'POST', { email, code, new_password });
        showLocalizedMessage(successMsg, 'forgot.reset_success');
        // Câu thông báo ở trên nằm trong modal quên mật khẩu, đóng modal là mất.
        showToast(t('forgot.reset_success'));
        closeForgotModal();
        setSubmitState(submitBtn, 'forgot.reset_submit', false);
    } catch (err) {
        setSubmitState(submitBtn, 'forgot.reset_submit', false);
        showApiMessage(errorMsg, err.message);
    }
});
