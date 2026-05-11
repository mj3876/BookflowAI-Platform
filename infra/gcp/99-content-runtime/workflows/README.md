# Workflows

1. 이 디렉토리의 용도
이 폴더는 독립적인 Google Cloud Workflows YAML 파일들을 저장하기 위해 마련된 공간입니다.

2. 현재 워크플로 관리 방식 (Inline 방식)
현재 BookFlow 프로젝트의 주요 워크플로들은 YAML 파일로 분리되지 않고, 테라폼(Terraform) 코드 내부에 직접(inline) 정의되어 있습니다.

../workflow.tf: bookflow-gcs-router (방금 우혁님이 수정하신 그 워크플로) 정의.

../gcs-staging-cleanup.tf: 스테이징 버킷 정리 워크플로 정의.

3. 왜 파일을 분리하지 않고 테라폼 안에 두었나? (핵심 이유)
문법 충돌 방지: 테라폼의 변수 치환 문법(${...})과 Google Workflows의 자체 표현식 문법($${...}) 사이에서 발생할 수 있는 의도치 않은 오류(accidental breakage)를 막기 위함입니다.

의존성 관리: 워크플로가 테라폼으로 생성되는 Cloud Function의 URL, 서비스 계정, 버킷 명, 프로젝트 변수 등에 강하게 의존하고 있기 때문에, 한곳(Terraform)에서 관리하는 것이 더 안전하다고 판단한 것입니다.

4. 언제 이 디렉토리로 옮겨야 하는가?
워크플로 YAML 파일의 내용이 너무 길어져서 가독성이 떨어질 때.

테라폼의 templatefile() 함수를 사용해야 할 정도로 복잡해질 때.

워크플로 전용 린팅(Linting)이나 테스트 도구를 별도로 사용해야 할 때.