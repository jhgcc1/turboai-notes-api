"""Auth serializers."""

from __future__ import annotations

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_email(self, value: str) -> str:
        email = value.lower().strip()
        if (
            User.objects.filter(username=email).exists()
            or User.objects.filter(email=email).exists()
        ):
            raise serializers.ValidationError("A user with this email already exists.")
        return email

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def create(self, validated_data: dict) -> User:
        email = validated_data["email"]
        user = User.objects.create_user(
            username=email,
            email=email,
            password=validated_data["password"],
        )
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs: dict) -> dict:
        email = attrs["email"].lower().strip()
        user = authenticate(
            request=self.context.get("request"),
            username=email,
            password=attrs["password"],
        )
        if user is None:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account is disabled.")
        attrs["user"] = user
        attrs["email"] = email
        return attrs


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "date_joined")
        read_only_fields = fields
