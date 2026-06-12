from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

options = Options()
options.add_argument('--user-data-dir=C:\\Users\\CEO\\AppData\\Local\\Google\\Chrome\\User Data')
options.add_argument('--profile-directory=Default')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_experimental_option('excludeSwitches', ['enable-automation'])
options.add_experimental_option('useAutomationExtension', False)

driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, 10)

try:
    # 네이버 블로그 검색
    driver.get('https://search.naver.com/search.naver?where=blog&query=여수유탑마리나')
    time.sleep(2.5)

    results = []
    # 블로그 결과 항목 수집
    items = driver.find_elements(By.CSS_SELECTOR, 'li.bx')
    if not items:
        items = driver.find_elements(By.CSS_SELECTOR, '.view_wrap')
    if not items:
        items = driver.find_elements(By.CSS_SELECTOR, 'div.total_area')

    print(f'블로그 검색 결과 항목 수: {len(items)}')

    for i, item in enumerate(items[:5]):
        try:
            title_el = item.find_element(By.CSS_SELECTOR, 'a.title_link, .api_txt_lines.total_tit, a[class*="title"]')
            title = title_el.text.strip()
            link = title_el.get_attribute('href')
        except:
            title = '제목 없음'
            link = ''

        try:
            desc_el = item.find_element(By.CSS_SELECTOR, 'div.api_txt_lines, .dsc_txt, a.dsc_link, div[class*="dsc"]')
            desc = desc_el.text.strip()
        except:
            desc = '내용 없음'

        results.append({'idx': i+1, 'title': title, 'desc': desc, 'link': link})
        print(f'\n[{i+1}] 제목: {title}')
        print(f'    요약: {desc[:200]}')
        print(f'    링크: {link}')

    # 첫 번째 블로그 글 본문 접근
    if results and results[0]['link']:
        print('\n\n===== 첫 번째 블로그 본문 분석 =====')
        driver.execute_script('window.open(arguments[0]);', results[0]['link'])
        time.sleep(1.5)
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(2.5)

        # iframe 안에 본문이 있을 수 있음
        try:
            iframe = driver.find_element(By.ID, 'mainFrame')
            driver.switch_to.frame(iframe)
        except:
            pass

        try:
            body_el = driver.find_element(By.CSS_SELECTOR, 'div#postViewArea, div.se-main-container, div#post-area, div.post-view')
            body_text = body_el.text.strip()[:800]
            print(f'본문 내용:\n{body_text}')
        except:
            body_text = driver.find_element(By.TAG_NAME, 'body').text[:800]
            print(f'본문(전체):\n{body_text}')

except Exception as e:
    print(f'오류 발생: {e}')
finally:
    time.sleep(3)
    driver.quit()
    print('\n스크래핑 완료')
