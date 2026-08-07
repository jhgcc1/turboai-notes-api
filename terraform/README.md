# Bootstrap once (local, with SSO) before remote state works:
#   aws s3 mb s3://turboai-notes-tfstate-615737882760 --region us-east-1
#   aws dynamodb create-table --table-name turboai-notes-tflock \
#     --attribute-definitions AttributeName=LockID,AttributeType=S \
#     --key-schema AttributeName=LockID,KeyType=HASH \
#     --billing-mode PAY_PER_REQUEST --region us-east-1
